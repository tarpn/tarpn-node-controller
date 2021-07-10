import threading
from io import BytesIO

from tarpn.crc import crc_b
from tarpn.log import LoggingMixin
from tarpn.network import QoS
from tarpn.network.mesh.header import NetworkHeader, BroadcastHeader, Protocol
from tarpn.network.mesh.protocol import MeshProtocol, encode_partial_packet
from tarpn.transport import L4Protocol
from tarpn.transport.mesh import L4Handler
from tarpn.transport.mesh.reliable import ReliableManager
from tarpn.util import TTLCache, WallTime


class BroadcastProtocol(L4Handler, LoggingMixin):
    def __init__(self, network: MeshProtocol, transport: L4Protocol, reliable_manager: ReliableManager):
        LoggingMixin.__init__(self)
        self.network = network
        self.transport = transport
        self.reliable_manager = reliable_manager

        self.send_seq: int = 1
        self.seq_lock = threading.Lock()

        self.ttl_cache = TTLCache(WallTime(), 30_000)

    def next_sequence(self) -> int:
        with self.seq_lock:
            seq = self.send_seq
            self.send_seq += 1
        return seq % 0x10000

    def handle_l4(self, network_header: NetworkHeader, stream: BytesIO):
        broadcast_header = BroadcastHeader.decode(stream)
        self.debug(f"Handling {broadcast_header}")

        if self.ttl_cache.contains(hash((broadcast_header.source, broadcast_header.sequence))):
            return

        data = stream.read()

        # Now forward it to everyone we've heard from recently
        neighbors = self.network.neighbors(since=300)
        if len(neighbors) == 0:
            self.debug("Skipping forwarding since we have no neighbors.")
            self.network.send_discovery()
        for neighbor in neighbors:
            if neighbor == broadcast_header.source:
                continue
            network_header = NetworkHeader(
                version=0,
                qos=QoS.Default,
                protocol=Protocol.BROADCAST,
                ttl=1,
                identity=self.network.next_sequence(),
                length=0,
                source=self.network.our_address,
                destination=neighbor,
            )
            buffer = encode_partial_packet([broadcast_header], data)
            self.info(f"Forwarding {broadcast_header} to {neighbor}: {data}")
            self.reliable_manager.send(network_header, buffer)

        # Now deliver it locally
        self.info(f"Delivering {broadcast_header}: {data}")
        self.transport.handle_broadcast(broadcast_header.source, broadcast_header.port, data)

    def send_broadcast(self, port: int, data: bytes):
        neighbors = self.network.neighbors(since=300)
        if len(neighbors) == 0:
            self.debug(f"Skipping broadcast since we have no neighbors.")
        for neighbor in neighbors:
            network_header = NetworkHeader(
                version=0,
                qos=QoS.Default,
                protocol=Protocol.BROADCAST,
                ttl=1,
                identity=self.network.next_sequence(),
                length=0,
                source=self.network.our_address,
                destination=neighbor,
            )
            broadcast_header = BroadcastHeader(
                source=self.network.our_address,
                port=port,
                sequence=self.next_sequence(),
                length=len(data),
                checksum=crc_b(data)
            )
            buffer = encode_partial_packet([broadcast_header], data)
            self.info(f"Broadcasting {broadcast_header} to {neighbor}: {data}")
            self.reliable_manager.send(network_header, buffer)
