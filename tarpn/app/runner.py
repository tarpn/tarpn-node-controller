import argparse
import asyncio
import importlib

from asyncio import Protocol, transports
from functools import partial
from typing import Optional

from tarpn.ax25 import parse_ax25_call, AX25Call
from tarpn.util import backoff


class Context:
    def __init__(self, transport):
        self.transport = transport

    def open(self, address):
        payload = bytearray()
        payload.append(0x00)
        payload.append(0x02)
        ax25_address = AX25Call.parse(address)
        ax25_address.write(payload)
        self.transport.write(payload)

    def write(self, address, data):
        payload = bytearray()
        payload.append(0x00)
        payload.append(0x01)
        ax25_address = AX25Call.parse(address)
        ax25_address.write(payload)
        payload.append(len(data))
        payload.extend(data)
        self.transport.write(payload)

    def close(self, address):
        payload = bytearray()
        payload.append(0x00)
        payload.append(0x03)
        ax25_address = AX25Call.parse(address)
        ax25_address.write(payload)
        self.transport.write(payload)


class AppRunnerProtocol(Protocol):
    """
    This class reads data off the unix domain socket and passes it to the application's entry point
    as the "app.input" key in the given dictionary. Values returned from the app module are then
    written back out to the domain socket.
    """
    def __init__(self, app_factory, callsign):
        self.app_factory = app_factory
        self.app_call = callsign
        self.app_instance = None
        self.transport = None
        self.closed = False

    def connection_made(self, transport: transports.BaseTransport) -> None:
        print(f"connection_made to {transport.get_extra_info('peername')}")
        self.transport = transport
        self.app_instance = self.app_factory(Context(transport), {
            "app.call": self.app_call
            #  TODO pass in other env here
        })

    def data_received(self, data: bytes) -> None:
        print("data_received from engine")
        bytes_iter = iter(data)
        proto_version = next(bytes_iter)

        if proto_version != 0x00:
            raise RuntimeError(f"Unexpected app protocol version {proto_version}")

        message_type = next(bytes_iter)
        if message_type == 0x01:  # data
            remote_call = parse_ax25_call(bytes_iter)
            data_len = next(bytes_iter)
            info = bytes(bytes_iter)
            if next(bytes_iter, None):
                raise BufferError(f"Underflow exception, did not expect any more bytes here. {repr(data)}")
            if len(info) != data_len:
                raise BufferError(f"Info actual length does not match indicated length. {repr(data)}")
            getattr(self.app_instance, "on_data")(str(remote_call), info)
        elif message_type == 0x02:  # connect
            remote_call = parse_ax25_call(bytes_iter)
            if next(bytes_iter, None):
                raise BufferError(f"Underflow exception, did not expect any more bytes here. {repr(data)}")
            getattr(self.app_instance, "on_connect")(str(remote_call))
        elif message_type == 0x03:  # disconnect
            remote_call = parse_ax25_call(bytes_iter)
            if next(bytes_iter, None):
                raise BufferError(f"Underflow exception, did not expect any more bytes here. {repr(data)}")
            getattr(self.app_instance, "on_disconnect")(str(remote_call))
        else:  # unknown
            raise RuntimeError(f"Unknown message type {message_type}")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        # TODO what now? probably retry the connection forever
        self.transport = None
        self.closed = True
        print("connection_lost")


async def create_and_watch_connection(loop, factory, sock):
    retry_backoff_iter = backoff(1, 1.2, 10)
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
    """Usage: python runner.py app_module:app_entrypoint"""

    parser = argparse.ArgumentParser(description='Run a TARPN Python application')
    parser.add_argument("app_factory_module", help="Module and method to create application instance")
    parser.add_argument("callsign", help="Application's L3 callsign")
    parser.add_argument("sock", help="Domain socket to connect to")
    args = parser.parse_args()

    toks = args.app_factory_module.split(":")
    if len(toks) == 1:
        # Only a module
        app_factory = importlib.import_module(toks[0])
    elif len(toks) == 2:
        # Module + method
        module = importlib.import_module(toks[0])
        app_factory = getattr(module, toks[1])
    else:
        raise ValueError("Unsupported format for app_factory_module")

    loop = asyncio.get_event_loop()
    factory = partial(AppRunnerProtocol, app_factory, args.callsign)
    loop.create_task(create_and_watch_connection(loop, factory, args.sock))
    loop.run_forever()


if __name__ == "__main__":
    main()
