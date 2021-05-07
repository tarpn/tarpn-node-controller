import asyncio
from asyncio import transports, AbstractEventLoop
from dataclasses import dataclass
from functools import partial
from typing import Any, Optional, Dict

from asyncio.base_events import Server

from tarpn.log import LoggingMixin
from tarpn.network import L3Address
from tarpn.scheduler import CloseableThread
from tarpn.transport import Transport, Protocol, L4Address


@dataclass(eq=True, frozen=True)
class UnixAddress(L3Address):
    path: str

    def __str__(self):
        return f"unix://{self.path}"


class AsyncioProtocolAdaptor(asyncio.Protocol):
    def __init__(self, protocol: Protocol, path: str):
        self.protocol = protocol
        self.transport = None
        self.path = path

    def data_received(self, data: bytes) -> None:
        self.protocol.data_received(data)

    def connection_made(self, transport: transports.Transport) -> None:
        self.transport = AsyncioTransportAdaptor(transport, self.path)
        self.protocol.connection_made(self.transport)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self.transport = None
        self.protocol.connection_lost(exc)


class AsyncioTransportAdaptor(Transport):
    def __init__(self, transport: asyncio.Transport, path: str):
        super().__init__()
        self.transport = transport
        self.path = path

    def get_extra_info(self, name, default=None) -> Dict[str, Any]:
        return self.transport.get_extra_info(name, default)

    def is_closing(self) -> bool:
        return self.transport.is_closing()

    def close(self) -> None:
        self.transport.close()

    def write(self, data: Any) -> None:
        self.transport.write(data)

    def get_write_buffer_size(self) -> int:
        return self.transport.get_write_buffer_size()

    def local_address(self) -> Optional[UnixAddress]:
        return UnixAddress(self.path)

    def remote_address(self) -> Optional[UnixAddress]:
        return UnixAddress(self.path)


class UnixServerThread(CloseableThread, LoggingMixin):
    def __init__(self, path: str, protocol: Protocol):
        CloseableThread.__init__(self, f"Unix Server {path}")
        LoggingMixin.__init__(self)
        self.path = path
        self.protocol = protocol
        self.server: Optional[Server] = None
        self._loop: Optional[AbstractEventLoop] = None

    def run(self):
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._create_server(self.path, self.protocol))
        self._loop.close()

    def close(self):
        if self.server is not None and self.server.is_serving():
            self.debug(f"Closing Unix socket server at {self.path}")
            self.server.close()

    async def _create_server(self, path, protocol):
        asyncio_protocol = partial(AsyncioProtocolAdaptor, protocol, path)
        loop = asyncio.get_event_loop()
        self.server = await loop.create_unix_server(asyncio_protocol, path)
        await self.server.start_serving()
        self.debug(f"Finished starting Unix socket server at {path}")
        while True:
            if self.server.is_serving():
                await asyncio.sleep(1)
            else:
                break
        self.debug(f"Closed Unix socket server at {path}")
