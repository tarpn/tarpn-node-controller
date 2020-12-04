from asyncio import Protocol, BaseProtocol, Transport
from typing import Any


class TestTransport(Transport):
    def __init__(self, loop, protocol):
        super().__init__()
        self._loop = loop
        self._protocol: Protocol = protocol
        self._closed = False
        self._written = []

    def is_reading(self) -> bool:
        return not self._closed

    def get_protocol(self) -> BaseProtocol:
        return self._protocol

    def write(self, data: Any) -> None:
        self._written.append(data)

    def read(self, data: Any) -> None:
        self._protocol.data_received(data)

    def close(self):
        self._protocol.eof_received()
        self._closed = True

    def captured_writes(self):
        return self._written


def create_test_connection(loop, protocol_factory, *args, **kwargs):
    protocol = protocol_factory()
    transport = TestTransport(loop, protocol)
    protocol.connection_made(transport)
    return (transport, protocol)
