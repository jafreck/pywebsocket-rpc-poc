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

from node_http_request_pb2 import NodeHttpRequest, NodeHttpResponse


class WebRoute(NamedTuple):
    WebHandler = Callable[[aiohttp.web.Request], aiohttp.web.Response]

    path: str
    handler: WebHandler
    http_method: Callable[[str, WebHandler], None]


# run a web server (fake node agent/controller)
@asynccontextmanager
async def get_test_webserver(
    routes: List[WebRoute],
    host: str = "localhost",
    port: int = 8080,
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


async def run_test_webserver(host="localhost", port: int = 8080):
    async def respond_success_handler(request: aiohttp.web.Request):
        try:
            request_body = await request.text()
            print(f"received message: {request_body}")
            resp = aiohttp.web.Response(
                body=(request_body + "/answer").encode(),
                status=200,
                headers={"key2": "value2"},
            )

            return resp
        except Exception as e:
            print(f"exception raised when responding: {e}")
            return aiohttp.web.Response(body="", status=500)

    async with get_test_webserver(
        host=host,
        port=port,
        routes=[
            WebRoute(
                http_method=aiohttp.web.put, path="/", handler=respond_success_handler
            )
        ],
    ):
        # run forever
        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, default=8080)
    parser.add_argument("--host", type=str, default="localhost")

    args = parser.parse_args()

    asyncio.get_event_loop().run_until_complete(
        run_test_webserver(host=args.host, port=args.listen_port)
    )
