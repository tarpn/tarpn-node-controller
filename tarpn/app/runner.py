import argparse
import asyncio
import dataclasses
import importlib
import logging

from asyncio import Protocol, transports
from concurrent.futures._base import Executor
from concurrent.futures.thread import ThreadPoolExecutor
from functools import partial
from typing import Optional, Dict, cast, Any

import msgpack

from tarpn.app import AppPayload
from tarpn.ax25 import parse_ax25_call, AX25Call
from tarpn.log import LoggingMixin
from tarpn.util import backoff


class Context:
    def __init__(self, transport):
        self.transport = transport

    def open(self, address):
        payload = AppPayload(0, 2, bytearray())
        ax25_address = AX25Call.parse(address)
        ax25_address.write(payload.buffer)
        msg = msgpack.packb(dataclasses.asdict(payload))
        self.transport.write(msg)

    def write(self, address, data):
        payload = AppPayload(0, 1, bytearray())
        ax25_address = AX25Call.parse(address)
        ax25_address.write(payload.buffer)
        payload.buffer.extend(data)
        msg = msgpack.packb(dataclasses.asdict(payload))
        self.transport.write(msg)

    def close(self, address):
        payload = AppPayload(0, 3, bytearray())
        ax25_address = AX25Call.parse(address)
        ax25_address.write(payload.buffer)
        msg = msgpack.packb(dataclasses.asdict(payload))
        self.transport.write(msg)


class BaseApp:
    def __init__(self, environ: Dict[str, Any]):
        self.environ = environ


class NetworkApp(BaseApp):
    """
    An application that sends and receives network traffic.
    """
    def __init__(self, environ: Dict[str, str]):
        BaseApp.__init__(self, environ)

    def on_network_connect(self, address: str):
        pass

    def on_network_data(self, address: str, data: bytes):
        pass

    def on_network_disconnect(self, address: str):
        pass

    def connection_made(self, context: Context):
        pass

    def connection_lost(self):
        pass


class AppPlugin:
    def __init__(self, environ: Dict[str, Any]):
        self.environ = environ

    def network_app(self) -> NetworkApp:
        raise NotImplementedError

    def start(self, executor: Executor) -> None:
        pass

    def close(self) -> None:
        pass


class AppRunnerProtocol(Protocol, LoggingMixin):
    """
    This class reads data off the unix domain socket and passes it to the application's entry point
    as the "app.input" key in the given dictionary. Values returned from the app module are then
    written back out to the domain socket.
    """
    def __init__(self,
                 network_app: NetworkApp,
                 environ: Dict[str, Any],
                 callsign: str):
        LoggingMixin.__init__(self)
        self.environ = environ
        self.app_call = callsign
        self.network_app = network_app
        self.transport = None
        self.closed = False
        self.unpacker = msgpack.Unpacker()

    def connection_made(self, transport: transports.BaseTransport) -> None:
        self.info(f"Connection_made to {transport.get_extra_info('peername')}")
        self.transport = transport
        self.network_app.connection_made(Context(transport))

    def data_received(self, data: bytes) -> None:
        self.unpacker.feed(data)
        for unpacked in self.unpacker:
            payload = AppPayload(**unpacked)
            self.debug(f"Data received from engine {payload}")
            if payload.version != 0:
                raise RuntimeError(f"Unexpected app protocol version {payload.version}")

            bytes_iter = iter(payload.buffer)
            if payload.type == 0x01:  # Data
                remote_call = parse_ax25_call(bytes_iter)
                info = bytes(bytes_iter)
                getattr(self.network_app, "on_data")(str(remote_call), info)
            elif payload.type == 0x02:  # Connect
                remote_call = parse_ax25_call(bytes_iter)
                getattr(self.network_app, "on_connect")(str(remote_call))
            elif payload.type == 0x03:  # Disconnect
                remote_call = parse_ax25_call(bytes_iter)
                getattr(self.network_app, "on_disconnect")(str(remote_call))
            else:
                raise RuntimeError(f"Unknown message type {payload.type}")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        # TODO what now? probably retry the connection forever
        self.network_app.connection_lost()
        self.transport = None
        self.closed = True
        self.info("Connection lost")


async def create_and_watch_connection(loop, factory, sock):
    retry_backoff_iter = backoff(1, 1.2, 10)
    await asyncio.sleep(next(retry_backoff_iter))
    transport, protocol = None, None
    while True:
        try:
            if protocol is None:
                print(f"Connecting to {sock}")
                transport, protocol = await loop.create_unix_connection(factory, sock)
            elif protocol.closed:
                print(f"Attempting to reconnect to {sock}")
                transport, protocol = await loop.create_unix_connection(factory, sock)
        except Exception as err:
            print(f"Error {err} while connecting, trying again later")
        await asyncio.sleep(next(retry_backoff_iter))


def main():
    parser = argparse.ArgumentParser(description='Run a TARPN Python application')
    parser.add_argument("app_factory_module", help="Module and method to create application instance")
    parser.add_argument("callsign", help="Application's L3 callsign")
    parser.add_argument("sock", help="Domain socket to connect to")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(level=level, format="%(levelname)-8s %(asctime)s -- %(message)s")

    tokens = args.app_factory_module.split(":")
    if len(tokens) == 2:
        # Module + method
        module = importlib.import_module(tokens[0], package="tarpn.app.runner")
        app_class = getattr(module, tokens[1])
    else:
        raise ValueError("Unsupported format for app_factory_module, expected app_module:app_class")

    environ = {
        "app.call": args.callsign
    }
    app_plugin: AppPlugin = cast(AppPlugin, app_class(environ))

    def run_unix_socket_loop():
        loop = asyncio.new_event_loop()
        factory = partial(AppRunnerProtocol, app_plugin.network_app(), app_plugin.environ, args.callsign)
        loop.create_task(create_and_watch_connection(loop, factory, args.sock))
        loop.run_forever()

    executor = ThreadPoolExecutor()
    executor.submit(run_unix_socket_loop)
    app_plugin.start(executor)


if __name__ == "__main__":
    main()
