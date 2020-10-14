import argparse
import asyncio
import ssl
import uuid
from contextlib import asynccontextmanager
from enum import Enum
from typing import Awaitable, Callable, List, NamedTuple, Tuple

import aiohttp
import trustme
from pywebsocket_rpc import client, common, server
from pywebsocket_rpc.client import WebsocketClient
from pywebsocket_rpc.common import IncomingRequestHandler
from pywebsocket_rpc.server import Route, WebsocketServer

from node_http_request_pb2 import NodeHttpRequest, NodeHttpResponse

LISTEN_PORT = 1234


def get_port() -> int:
    global LISTEN_PORT
    LISTEN_PORT += 1
    return LISTEN_PORT


def construct_tls12_restrictive_ssl_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS)
    context.options |= ssl.OP_NO_TLSv1
    context.options |= ssl.OP_NO_TLSv1_1
    context.options |= ssl.OP_NO_SSLv2
    context.options |= ssl.OP_NO_SSLv3
    return context


def tls_certificate_authority() -> trustme.CA:
    return trustme.CA()


def tls_certificate(tls_certificate_authority: trustme.CA) -> trustme.LeafCert:
    return tls_certificate_authority.issue_server_cert("localhost", "127.0.0.1", "::1")


def server_ssl_ctx(tls_certificate: trustme.LeafCert) -> ssl.SSLContext:
    ssl_ctx = construct_tls12_restrictive_ssl_context()
    tls_certificate.configure_cert(ssl_ctx)
    return ssl_ctx


def client_ssl_ctx(tls_certificate_authority: trustme.CA) -> ssl.SSLContext:
    ssl_ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    tls_certificate_authority.configure_trust(ssl_ctx)
    return ssl_ctx


async def run_test_server(
    port: int,
    host: str = "localhost",
    ssl_context: ssl.SSLContext = None,
    routes: List[Route] = None,
    tokens: List[Tuple[str, common.Token]] = None,
) -> None:
    print(f"Running test server on {host}:{port}, ssl={ssl_context is not None}")

    server = WebsocketServer(
        host=host,
        port=port,
        ssl_context=ssl_context,
        tokens=dict(tokens) if tokens else {},
    )
    return await server.start(routes)


async def connect_test_client(
    port: int,
    incoming_request_handler: common.IncomingRequestHandler,
    host: str = "localhost",
    path: str = "/ws",
    ssl_context: ssl.SSLContext = None,
    token: Tuple[str, common.Token] = None,
) -> WebsocketClient:
    protocol = "https" if ssl_context is not None else "http"

    client = WebsocketClient(
        connect_address=f"{protocol}://{host}:{port}{path}",
        incoming_request_handler=incoming_request_handler,
        ssl_context=ssl_context,
        token=token[1] if token else None,
        id=token[0] if token else None,
    )
    await client.connect()
    return client


def generate_tokens(count: int) -> List[common.Token]:
    return [
        (str(uuid.uuid4()), common.Token(value=str(uuid.uuid4()))) for _ in range(count)
    ]


async def test_server_generated_request_make_http_request_success(
    port: int, server_ssl_ctx: ssl.SSLContext, client_ssl_ctx: ssl.SSLContext
):
    s_client = None  # type: server.ServerClient

    async def server_client_handler(
        request: aiohttp.web.Request,
        ws: aiohttp.web.WebSocketResponse,
    ) -> aiohttp.web.WebSocketResponse:
        nonlocal s_client
        client_id = request.headers["x-ms-node-id"]
        s_client = server.ServerClient(
            id=client_id,
            websocket=ws,
            incoming_request_handler=lambda x: x,
        )
        s_client.initialize()

        await s_client.receive_messages()
        return ws

    tokens = generate_tokens(1)
    await run_test_server(
        port=port,
        ssl_context=server_ssl_ctx,
        routes=[server.Route(path="/ws", handler=server_client_handler)],
        tokens=tokens,
    )

    async def message_relay_handler(data: bytes) -> bytes:
        """
        deserialize bytes as a protobuf message
        make an http call to another aiohttp web server on another port
        """
        node_http_req = NodeHttpRequest()
        node_http_req.ParseFromString(data)

        async with aiohttp.ClientSession() as session:
            try:
                async with session.put(
                    "http://localhost:8080/",
                    data=node_http_req.body,
                    headers=node_http_req.headers,
                ) as resp:
                    node_http_resp = NodeHttpResponse()
                    node_http_resp.status_code = resp.status
                    node_http_resp.body = await resp.read()

                    for key, value in resp.headers.items():
                        node_http_resp.headers[key] = value

                    return node_http_resp.SerializeToString()

            except Exception as e:
                print(f"put failed with: {e}")

    await connect_test_client(
        port=port,
        ssl_context=client_ssl_ctx,
        token=tokens[0],  # use valid token
        incoming_request_handler=message_relay_handler,
    )

    async def respond_success_handler(request: aiohttp.web.Request):
        try:
            resp = aiohttp.web.Response(
                body=(await request.text() + "/answer").encode(),
                status=200,
                headers={"key2": "value2"},
            )

            return resp
        except Exception as e:
            print(f"exception raised when responding: {e}")
            return aiohttp.web.Response(body="", status=500)

    async with get_test_webserver(
        port=8080,
        routes=[
            WebRoute(
                http_method=aiohttp.web.put,
                path="/",
                handler=respond_success_handler,
            )
        ],
    ):
        resp_bytes = await s_client.request(
            NodeHttpRequest(headers={"key": "value"}, body=b"test").SerializeToString()
        )
        resp_bytes = await s_client.request(
            NodeHttpRequest(headers={"key": "value"}, body=b"test2").SerializeToString()
        )

    node_http_resp = NodeHttpResponse()
    node_http_resp.ParseFromString(resp_bytes)


class WebRoute(NamedTuple):
    WebHandler = Callable[[aiohttp.web.Request], aiohttp.web.Response]

    path: str
    handler: WebHandler
    http_method: Callable[[str, WebHandler], None]


@asynccontextmanager
async def get_test_webserver(
    routes: List[WebRoute],
    host: str = "localhost",
    port: int = 80,
) -> None:
    app = aiohttp.web.Application()

    for route in routes:
        app.add_routes([route.http_method(route.path, route.handler)])

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, host, port)
    await site.start()

    try:
        yield
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port")
    parser.add_argument("--host")
    parser.add_argument("--node-agent-port")
    parser.add_argument("--controller-port")
    parser.add_argument("--ssl-cert")
    parser.add_argument("--service-url")
    parser.add_argument("--service-port")

    args = parser.parse_args()

    loop = asyncio.get_event_loop()

    ca = tls_certificate_authority()
    leaf_cert = tls_certificate(ca)
    ssl_ctx = server_ssl_ctx(leaf_cert)
    client_ctx = client_ssl_ctx(ca)

    loop.run_until_complete(
        test_server_generated_request_make_http_request_success(
            12345,
            ssl_ctx,
            client_ctx,
        )
    )
