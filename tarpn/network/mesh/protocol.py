import dataclasses
import threading
from datetime import datetime, timedelta
from functools import partial
from io import BytesIO
from typing import Tuple, List, Dict, Optional, Set

from tarpn.datalink import L2Payload
from tarpn.datalink.ax25_l2 import AX25Address
from tarpn.datalink.protocol import LinkMultiplexer
from tarpn.log import LoggingMixin
from tarpn.network import L3Protocol, L3Address, L3Payload, QoS
from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.header import Protocol, NetworkHeader, Header, DiscoveryHeader
from tarpn.scheduler import Scheduler
from tarpn.transport.mesh import L4Handlers
from tarpn.util import Time, TTLCache


def encode_packet(network_header: NetworkHeader, additional_headers: List[Header], payload: bytes) -> bytes:
    stream = BytesIO()
    network_header.encode(stream)
    for header in additional_headers:
        header.encode(stream)
    stream.write(payload)
    stream.seek(0)
    return stream.read()


def encode_partial_packet(headers: List[Header], payload: bytes) -> bytes:
    stream = BytesIO()
    for header in headers:
        header.encode(stream)
    stream.write(payload)
    stream.seek(0)
    return stream.read()


class MeshProtocol(L3Protocol, LoggingMixin):
    """
    A simple protocol for a partially connected mesh network.
    """

    ProtocolId = 0xB0
    WindowSize = 1024
    MaxFragments = 8
    HeaderBytes = 10
    DefaultTTL = 7
    BroadcastAddress = MeshAddress(0xFFFF)

    def __init__(self,
                 time: Time,
                 our_address: MeshAddress,
                 link_multiplexer: LinkMultiplexer,
                 l4_handlers: L4Handlers,
                 scheduler: Scheduler):
        LoggingMixin.__init__(self, extra_func=self.log_ident)
        self.time = time
        self.link_multiplexer = link_multiplexer
        self.our_address = our_address
        self.l4_handlers = l4_handlers

        # TTL cache of seen frames from each source
        self.header_cache: TTLCache = TTLCache(time, 30_000)

        # Our own send sequence
        self.send_seq: int = 1
        self.seq_lock = threading.Lock()

        # Mapping of neighbor address to last heard time and heard-on port
        self.neighbors_last_heard: Dict[MeshAddress, datetime] = dict()
        self.neighbors_heard_on: Dict[MeshAddress, int] = dict()
        self.discovery_timer = scheduler.timer(120_000, partial(self.send_discovery, None), auto_start=False)

        scheduler.timer(1_000, partial(self.send_discovery, None), auto_start=True)

    def __repr__(self):
        return f"<MeshProtocol {self.our_address}>"

    def log_ident(self) -> str:
        return f"[MeshProtocol {self.our_address}]"

    def next_sequence(self) -> int:
        with self.seq_lock:
            seq = self.send_seq
            self.send_seq += 1
        return seq % MeshProtocol.WindowSize

    def next_sequences(self, n) -> List[int]:
        seqs = []
        with self.seq_lock:
            for i in range(n):
                seqs.append(self.send_seq % MeshProtocol.WindowSize)
            self.send_seq += n
        return seqs

    def neighbors(self, since=300) -> Set[MeshAddress]:
        time_ago = datetime.utcnow() - timedelta(seconds=since)
        return {neighbor for neighbor, last_heard in self.neighbors_last_heard.items() if last_heard > time_ago}

    def can_handle(self, protocol: int) -> bool:
        return protocol == MeshProtocol.ProtocolId

    def handle_l2_payload(self, payload: L2Payload):
        stream = BytesIO(payload.l3_data)
        header = NetworkHeader.decode(stream)

        # Handle L3 protocols first
        if header.protocol == Protocol.DISCOVER:
            self.handle_discovery(payload.link_id, header, DiscoveryHeader.decode(stream))
            return

        # Now decide if we should handle or drop
        if self.header_cache.contains(hash(header)):
            return

        self.debug(f"Handling {header}")

        # If the packet is addressed to us, handle it
        if header.destination in (self.our_address, self.BroadcastAddress):
            self.l4_handlers.handle_l4(header, header.protocol, stream)

        if header.ttl > 1:
            # Decrease the TTL
            header_copy = dataclasses.replace(header, ttl=header.ttl - 1)
            stream.seek(0)
            header_copy.encode(stream)
            stream.seek(0)
            self.send(header_copy, stream.read(), exclude_link_id=payload.link_id)
        else:
            self.debug(f"Not forwarding {header} due to TTL")

    def handle_discovery(self, link_id: int, network_header: NetworkHeader, discover: DiscoveryHeader):
        """
        Handling an inbound DISCOVER packet. This is used as a heartbeat and for neighbor discovery
        """
        self.debug(f"Handling {discover}")
        if network_header.source not in self.neighbors_last_heard:
            self.neighbors_last_heard[network_header.source] = datetime.utcnow()
            self.neighbors_heard_on[network_header.source] = link_id
            self.info(f"Found new neighbor {network_header.source}!")
            self.send_discovery(link_id)
        elif len(discover.neighbors) == 0:
            self.neighbors_last_heard[network_header.source] = datetime.utcnow()
            self.send_discovery(link_id)
        else:
            self.neighbors_last_heard[network_header.source] = datetime.utcnow()

    def send_discovery(self, link: Optional[int] = None):
        """
        Send out a DISCOVER packet with our known neighbors
        """
        now = datetime.utcnow()
        discovery = DiscoveryHeader([], [])
        for neighbor, last_seen in self.neighbors_last_heard.items():
            discovery.neighbors.append(neighbor)
            discovery.last_seen_s.append((now - last_seen).seconds)

        network_header = NetworkHeader(
            version=0,
            qos=QoS.Lower,
            protocol=Protocol.DISCOVER,
            ttl=1,
            identity=self.next_sequence(),
            length=0,
            source=self.our_address,
            destination=self.BroadcastAddress,
        )

        stream = BytesIO()
        network_header.encode(stream)
        discovery.encode(stream)
        stream.seek(0)
        buffer = stream.read()

        if link is not None:
            links = [link]
        else:
            links = self.link_multiplexer.links_for_address(AX25Address("TAPRN"))

        for link_id in links:
            payload = L3Payload(
                source=network_header.source,
                destination=network_header.destination,
                protocol=MeshProtocol.ProtocolId,
                buffer=buffer,
                link_id=link_id,
                qos=QoS.Lower,
                reliable=False)
            self.debug(f"Sending Discover {payload}")
            self.link_multiplexer.offer(payload)
        self.discovery_timer.reset()

    def send(self, header: NetworkHeader, buffer: bytes, exclude_link_id: Optional[int] = None):
        """
        Send a packet to a network destination. If the destination address is ff.ff, the packet
        is broadcast on all available L2 links (optionally excluding a given link).

        :param header the header of the packet to broadcast
        :param buffer the entire buffer of the packet to broadcast
        :param exclude_link_id an L2 link to exclude from the broadcast
        """

        if header.destination == MeshProtocol.BroadcastAddress or header.destination not in self.neighbors_heard_on:
            links = self.link_multiplexer.links_for_address(AX25Address("TAPRN"), exclude_link_id)
        else:
            links = [self.neighbors_heard_on[header.destination]]

        for link_id in links:
            payload = L3Payload(
                source=header.source,
                destination=header.destination,
                protocol=MeshProtocol.ProtocolId,
                buffer=buffer,
                link_id=link_id,
                qos=QoS(header.qos),
                reliable=False)
            self.debug(f"Forwarding {payload}")
            self.link_multiplexer.offer(payload)

    def mtu(self):
        # We want uniform packets, so get the min L2 MTU
        return self.link_multiplexer.mtu() - MeshProtocol.HeaderBytes

    def route_packet(self, address: L3Address) -> Tuple[bool, int]:
        # Subtract L3 header size and multiply by max fragments
        l3_mtu = MeshProtocol.MaxFragments * (self.mtu() - MeshProtocol.HeaderBytes)
        return True, l3_mtu

    def send_packet(self, payload: L3Payload) -> bool:
        return self.link_multiplexer.offer(payload)

    def listen(self, address: MeshAddress):
        # By default we listen for all addresses
        pass

    def register_transport_protocol(self, protocol) -> None:
        # TODO remove this from L3Protocol
        pass
