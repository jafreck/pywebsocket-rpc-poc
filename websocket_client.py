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
from pywebsocket_rpc.common import IncomingRequestHandler, Token
from pywebsocket_rpc.server import Route, ServerClient, WebsocketServer

from node_http_request_pb2 import NodeHttpRequest, NodeHttpResponse, Target


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
    # port: int,
    server_ssl_ctx: ssl.SSLContext,
    client_ssl_ctx: ssl.SSLContext,
    # host: str,
    node_agent_port: int,
    node_controller_port: int,
    service_url: str,
    service_port: int,
    id: str,
    token: str,
):
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
                    url="http://localhost:{0}/".format(
                        node_agent_port
                        if node_http_req.target == Target.NodeAgent
                        else node_controller_port
                    ),
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
                node_http_resp = NodeHttpResponse()
                node_http_resp.status_code = 500
                return node_http_resp.SerializeToString()

    async with await connect_test_client(
        host=service_url,
        port=service_port,
        ssl_context=client_ssl_ctx,
        token=(id, Token(value=token)),  # use valid token
        incoming_request_handler=message_relay_handler,
    ):
        # sleep forever
        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument("--node-agent-port", type=int, default=8080)
    parser.add_argument("--node-controller-port", type=int, default=8081)
    parser.add_argument("--service-url", type=str, default="localhost")
    parser.add_argument("--service-port", type=int, default=8082)
    parser.add_argument("--token", type=str, required=True)
    parser.add_argument("--id", type=str, required=True)

    args = parser.parse_args()

    loop = asyncio.get_event_loop()

    # TODO: use cert from command line instead of this
    ca = tls_certificate_authority()
    leaf_cert = tls_certificate(ca)
    ssl_ctx = server_ssl_ctx(leaf_cert)
    client_ctx = client_ssl_ctx(ca)

    # TODO: ssl currently disabled as ca is created in process, not shared
    loop.run_until_complete(
        test_server_generated_request_make_http_request_success(
            # port=args.listen_port,
            server_ssl_ctx=None,  # ssl_ctx,
            client_ssl_ctx=None,  # client_ctx,
            # host=args.host,
            node_agent_port=args.node_agent_port,
            node_controller_port=args.node_controller_port,
            service_url=args.service_url,
            service_port=args.service_port,
            id=args.id,
            token=args.token,
        )
    )
