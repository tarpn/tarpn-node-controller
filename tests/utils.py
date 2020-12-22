import asyncio
from asyncio import Protocol, BaseProtocol, Transport
from typing import Any


class TestTransport(Transport):
    def __init__(self, loop, protocol):
        super().__init__()
        self._loop = loop
        self._protocol: Protocol = protocol
        self._closed = False
        self._paused = False

    def start(self, inbound: asyncio.Queue, outbound: asyncio.Queue):
        self._outbound = outbound
        asyncio.create_task(self._run(inbound))

    async def _run(self, inbound: asyncio.Queue):
        while not self._closed:
            if self._paused:
                await asyncio.sleep(0.001)
                continue
            item = await inbound.get()
            if item:
                self._protocol.data_received(item)
                inbound.task_done()

    def is_reading(self) -> bool:
        return not self._closed

    def get_protocol(self) -> BaseProtocol:
        return self._protocol

    def write(self, data: Any) -> None:
        asyncio.create_task(self._outbound.put(data))

    def close(self):
        self._protocol.eof_received()
        self._closed = True

    def pause_reading(self) -> None:
        self._paused = True

    def resume_reading(self) -> None:
        self._paused = True


def create_test_connection(loop, protocol_factory, *args, **kwargs):
    protocol = protocol_factory()
    transport = TestTransport(loop, protocol)
    protocol.connection_made(transport)
    return (transport, protocol)