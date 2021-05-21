import argparse
import asyncio
import dataclasses
import importlib.util
import logging
import os
import sys

from asyncio import Protocol, transports, AbstractEventLoop
from functools import partial
from typing import Optional, Dict, cast, Any, Callable

import msgpack

from tarpn.application import AppPayload
from tarpn.log import LoggingMixin
from tarpn.scheduler import Scheduler
from tarpn.settings import Settings
from tarpn.util import backoff


class Context:
    def __init__(self, transport):
        self.transport = transport

    def open(self, address: str):
        payload = AppPayload(0, 2, address, bytes())
        msg = msgpack.packb(dataclasses.asdict(payload))
        self.transport.write(msg)

    def write(self, address: str, data: bytes):
        payload = AppPayload(0, 1, address, bytes(data))
        msg = msgpack.packb(dataclasses.asdict(payload))
        self.transport.write(msg)

    def close(self, address: str):
        payload = AppPayload(0, 3, address, bytes())
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
    def __init__(self, scheduler: Scheduler, environ: Dict[str, Any]):
        self.scheduler = scheduler
        self.environ = environ

    def network_app(self, loop: AbstractEventLoop) -> NetworkApp:
        raise NotImplementedError

    def start(self) -> None:
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
                 environ: Dict[str, Any]):
        LoggingMixin.__init__(self)
        self.environ = environ
        self.network_app = network_app
        self.transport = None
        self.closed = False
        self.unpacker = msgpack.Unpacker()

    def connection_made(self, transport: transports.BaseTransport) -> None:
        self.info(f"Connected to packet engine at {transport.get_extra_info('peername')}")
        self.transport = transport
        self.network_app.connection_made(Context(transport))

    def data_received(self, data: bytes) -> None:
        self.unpacker.feed(data)
        for unpacked in self.unpacker:
            payload = AppPayload(**unpacked)
            self.debug(f"Data received from engine {payload}")
            if payload.version != 0:
                raise RuntimeError(f"Unexpected app protocol version {payload.version}")

            if payload.type == 0x01:  # Data
                info = bytes(payload.buffer)
                getattr(self.network_app, "on_network_data")(payload.address, info)
            elif payload.type == 0x02:  # Connect
                getattr(self.network_app, "on_network_connect")(payload.address)
            elif payload.type == 0x03:  # Disconnect
                getattr(self.network_app, "on_network_disconnect")(payload.address)
            else:
                raise RuntimeError(f"Unknown message type {payload.type}")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self.network_app.connection_lost()
        self.transport = None
        self.closed = True
        self.info("Disconnected from packet engine")


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
    parser.add_argument("app", help="The name of the app to run")
    parser.add_argument("config", nargs="?", default="config/node.ini", help="Config file")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(level=level, format="%(levelname)-8s %(asctime)s -- %(message)s")

    s = Settings(".", ["config/defaults.ini", args.config])
    config = None
    for app_config in s.app_configs():
        if app_config.app_name() == args.app:
            config = app_config
    if config is None:
        logging.error(f"No such app {args.app} defined in {args.config}")
        sys.exit(1)

    if "app.module" in config:
        module = importlib.import_module(config.get("app.module"), "app")
    elif "app.file" in config:
        py_file = os.path.abspath(config.get("app.file"))
        spec = importlib.util.spec_from_file_location("app", py_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        logging.error("Either app.file or app.module must be specified in app configuration")
        sys.exit(1)

    app_class = getattr(module, config.get("app.class"))
    environ = {}
    for key, value in config.as_dict().items():
        if key.startswith("env."):
            environ[key[4:]] = value

    scheduler = Scheduler()
    app_plugin: AppPlugin = cast(AppPlugin, app_class(scheduler, environ))

    def run_unix_socket_loop():
        loop = asyncio.new_event_loop()
        network_app = app_plugin.network_app(loop)
        factory = partial(AppRunnerProtocol, network_app, app_plugin.environ)
        loop.create_task(create_and_watch_connection(loop, factory, config.app_socket()))
        loop.run_forever()

    scheduler.run(run_unix_socket_loop)
    app_plugin.start()
    try:
        # Wait for all threads
        scheduler.join()
        pass
    except KeyboardInterrupt:
        scheduler.shutdown()
        pass

if __name__ == "__main__":
    main()
