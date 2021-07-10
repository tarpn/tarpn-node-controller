import secrets
import struct
from io import BytesIO

from tarpn.log import LoggingMixin
from tarpn.network import L3Protocol, QoS
from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.header import DatagramHeader, NetworkHeader, Protocol, ReliableHeader, ReliableFlags, Datagram
from tarpn.network.mesh.protocol import MeshProtocol, encode_packet
from tarpn.transport.mesh.fragment import FragmentProtocol
from tarpn.transport import L4Protocol
from tarpn.transport.mesh import L4Handler
from tarpn.transport.mesh.reliable import ReliableProtocol


class DatagramProtocol(L4Handler, LoggingMixin):
    def __init__(self,
                 network: L3Protocol,
                 datagram_manager: L4Protocol,
                 fragment_protocol: FragmentProtocol,
                 reliable_protocol: ReliableProtocol):
        LoggingMixin.__init__(self)
        self.network = network
        self.datagram_manager = datagram_manager
        self.fragment_protocol = fragment_protocol
        self.reliable_protocol = reliable_protocol

    def send_datagram(self,
                      source: MeshAddress, destination: MeshAddress,
                      datagram_header: DatagramHeader, data: bytes, reliable: bool = False):
        if reliable:
            network_header = NetworkHeader(
                version=0,
                qos=QoS.Default,
                protocol=Protocol.RELIABLE,
                ttl=MeshProtocol.DefaultTTL,
                identity=struct.unpack('<H', secrets.token_bytes(2))[0],
                length=0,
                source=source,
                destination=destination,
            )
            reliable_header = ReliableHeader(
                protocol=Protocol.DATAGRAM,
                flags=ReliableFlags.ACK,
                sequence=network_header.identity,
                acknowledged=[]
            )
            if len(data) + DatagramHeader.size() + reliable_header.size() > self.network.mtu():
                raise RuntimeError("Fragmentation not supported for reliable protocol")
            buffer = BytesIO()
            network_header.encode(buffer)
            reliable_header.encode(buffer)
            datagram_header.encode(buffer)
            buffer.write(data)
            buffer.seek(0)
            self.reliable_protocol.send(network_header, reliable_header, buffer.read())
        else:
            if len(data) + DatagramHeader.size() > self.network.mtu():
                self.fragment_protocol.fragment_datagram(source, destination, datagram_header, data)
            else:
                network_header = NetworkHeader(
                    version=0,
                    qos=QoS.Default,
                    protocol=Protocol.DATAGRAM,
                    ttl=MeshProtocol.DefaultTTL,
                    identity=struct.unpack('<H', secrets.token_bytes(2))[0],
                    length=0,
                    source=source,
                    destination=destination,
                )
                buffer = encode_packet(network_header, [datagram_header], data)
                self.network.send(network_header, buffer)

    def handle_l4(self, network_header: NetworkHeader, stream: BytesIO):
        datagram_header = DatagramHeader.decode(stream)
        self.datagram_manager.handle_datagram(
            Datagram(network_header, datagram_header, stream.read())
        )