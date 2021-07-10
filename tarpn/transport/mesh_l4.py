import urllib.parse
from dataclasses import dataclass
from typing import Tuple, Dict, Any, Callable, Optional

from tarpn.crc import crc_b
from tarpn.log import LoggingMixin
from tarpn.network import L3Protocol, L3Address
from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.header import DatagramHeader, Datagram
from tarpn.transport.mesh.broadcast import BroadcastProtocol
from tarpn.transport.mesh.datagram import DatagramProtocol
from tarpn.transport import DatagramProtocol as DProtocol
from tarpn.transport import DatagramTransport as DTransport
from tarpn.transport import L4Protocol, Transport, L4Address


@dataclass(eq=True, frozen=True)
class MeshTransportAddress(L4Address):
    address: MeshAddress
    port: int

    def network_address(self) -> MeshAddress:
        return self.address

    def __repr__(self):
        return f"mesh://{self.address}:{self.port}"

    @classmethod
    def parse(cls, s: str):
        uri_parts = urllib.parse.urlsplit(s)
        assert uri_parts.scheme == "mesh"
        address = MeshAddress.parse(uri_parts.hostname)
        return cls(address=address, port=uri_parts.port)


class DatagramTransport(DTransport):
    """
    A channel for sending and receiving datagrams
    """

    def __init__(self,
                 network: L3Protocol,
                 protocol: BroadcastProtocol,
                 port: int,
                 local: MeshAddress,
                 remote: Optional[MeshAddress]):
        Transport.__init__(self, extra={"peername": ("", port)})
        self.network = network
        self.protocol = protocol
        self.port = port
        self.local = local
        self.remote = remote
        self.closing = False

    def is_closing(self) -> bool:
        return self.closing

    def close(self) -> None:
        self.closing = True

    def write(self, data: Any) -> None:
        if self.remote is not None:
            self.write_to(str(self.remote), data)
        else:
            raise RuntimeError("Use write_to with unconnected datagram transport")

    def write_to(self, address: str, data: Any) -> None:
        dest = MeshAddress.parse(address)
        can_route, mtu = self.network.route_packet(dest)

        if can_route:
            if isinstance(data, str):
                encoded_data = data.encode("utf-8")
            elif isinstance(data, (bytes, bytearray)):
                encoded_data = data
            else:
                raise ValueError("DatagramTransport.write only supports bytes and strings")
            if len(encoded_data) <= mtu:
                self.protocol.send_broadcast(self.port, encoded_data)
            else:
                raise RuntimeError(f"Message too large, maximum size is {mtu}")
        else:
            raise RuntimeError(f"Cannot route to {MeshAddress(self.port)}!")

    def get_write_buffer_size(self) -> int:
        return 1000

    def local_address(self) -> Optional[L3Address]:
        return self.local

    def remote_address(self) -> Optional[L3Address]:
        return self.remote


class MeshTransportManager(L4Protocol, LoggingMixin):
    def __init__(self, l3_protocol: L3Protocol):
        LoggingMixin.__init__(self)
        self.l3_protocol = l3_protocol
        self.connections: Dict[int, Tuple[Transport, DProtocol]] = dict()
        self.l3_protocol.register_transport_protocol(self)
        self.broadcast_protocol: Optional[BroadcastProtocol] = None

    def bind(self, protocol_factory: Callable[[], DProtocol],
             local_address: MeshAddress, port: int) -> DProtocol:
        if port in self.connections:
            raise RuntimeError(f"Connection to {port} is already open")
        protocol = protocol_factory()
        transport = DatagramTransport(self.l3_protocol, self.broadcast_protocol, port, local_address, None)
        protocol.connection_made(transport)
        self.connections[port] = (transport, protocol)
        return protocol

    def connect(self, protocol_factory: Callable[[], DProtocol],
                local_address: MeshAddress, remote_address: MeshAddress, port: int) -> DProtocol:
        if port in self.connections:
            raise RuntimeError(f"Connection to {port} is already open")
        protocol = protocol_factory()
        transport = DatagramTransport(self.l3_protocol, self.broadcast_protocol, port, local_address, remote_address)
        protocol.connection_made(transport)
        self.connections[port] = (transport, protocol)
        return protocol

    def handle_datagram(self, datagram: Datagram):
        port = datagram.datagram_header.destination
        if port in self.connections:
            address = MeshTransportAddress(datagram.network_header.source, port)
            self.connections[port][1].datagram_received(datagram.payload, address)
        else:
            self.warning(f"No listeners for datagram {datagram}, ignoring.")

    def handle_broadcast(self, source: MeshAddress, port: int, payload: bytes):
        if port in self.connections:
            address = MeshTransportAddress(source, port)
            self.connections[port][1].datagram_received(payload, address)
        else:
            self.warning(f"No listeners on {port}, ignoring broadcast.")
