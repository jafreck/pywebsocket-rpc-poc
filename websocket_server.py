import argparse
import asyncio
import ssl
import uuid
from contextlib import asynccontextmanager
from enum import Enum
from typing import Awaitable, Callable, List, NamedTuple, Tuple
import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor

import aiohttp
import trustme
from pywebsocket_rpc import client, common, server
from pywebsocket_rpc.client import WebsocketClient
from pywebsocket_rpc.common import IncomingRequestHandler, Token
from pywebsocket_rpc.server import Route, ServerClient, WebsocketServer

from node_http_request_pb2 import NodeHttpRequest, NodeHttpResponse


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
        routes=routes,
    )
    return await server.start()


# run a test server (fake nodex)
async def run_test_websocket_server(
    host: str,
    port: int,
    server_ssl_ctx: ssl.SSLContext,
    tokens: List[Tuple[str, Token]],
) -> ServerClient:
    s_client = None  # type: server.ServerClient

    async def server_client_handler(
        request: aiohttp.web.Request,
        ws: aiohttp.web.WebSocketResponse,
    ) -> aiohttp.web.WebSocketResponse:
        nonlocal s_client
        client_id = request.headers["x-ms-node-id"]
        print("creating server client")
        s_client = ServerClient(
            id=client_id,
            websocket=ws,
            incoming_request_handler=lambda x: x,
        )
        s_client.initialize()

        await s_client.receive_messages()
        return ws

    server = WebsocketServer(
        host=host,
        port=port,
        ssl_context=server_ssl_ctx,
        tokens=dict(tokens) if tokens else {},
        routes=[Route(path="/ws", handler=server_client_handler)],
    )

    await server.start()

    while True:
        msg = await ainput("new message: ")
        resp_bytes = await s_client.request(
            NodeHttpRequest(
                headers={"key": "value"}, body=msg.encode()
            ).SerializeToString()
        )

        node_http_resp = NodeHttpResponse()
        node_http_resp.ParseFromString(resp_bytes)

        print(f"response: {node_http_resp.body}")

    return s_client


async def ainput(prompt: str = ""):
    with ThreadPoolExecutor(
        1, "AsyncInput", lambda x: print(x, end="", flush=True), (prompt,)
    ) as executor:
        return (
            await asyncio.get_event_loop().run_in_executor(executor, sys.stdin.readline)
        ).rstrip()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, default=8082)
    parser.add_argument("--host", type=str, default="localhost")
    parser.add_argument("--token", type=str, required=True)
    parser.add_argument("--id", type=str, required=True)

    args = parser.parse_args()
    asyncio.get_event_loop().run_until_complete(
        run_test_websocket_server(
            server_ssl_ctx=None,
            host=args.host,
            port=args.listen_port,
            tokens=[(args.id, Token(value=args.token))],
        )
    )
