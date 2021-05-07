import dataclasses
import operator
from collections import defaultdict
from datetime import datetime
from io import BytesIO
from typing import Tuple, List, Dict, Optional, Any, Set, cast

from tarpn.datalink import L2Payload
from tarpn.datalink.ax25_l2 import AX25Address
from tarpn.datalink.protocol import LinkMultiplexer
from tarpn.log import LoggingMixin
from tarpn.network import L3Protocol, L3Address, L3Payload, QoS
from tarpn.network.mesh.header import Fragment, PDU, Datagram, DatagramHeader, \
    Protocol, Raw, PacketHeader, Announce, FragmentHeader, Header, Flags
from tarpn.network.mesh import MeshAddress
from tarpn.scheduler import Scheduler
from tarpn.transport import L4Protocol
from tarpn.util import Time, ByteUtils


class FragmentAssembler:
    # TODO expire fragments after some time
    # TODO support more L4 protocols here
    def __init__(self):
        # A buffer of undelivered fragment. Structured like source -> sequence -> fragments
        self.buffer: Dict[MeshAddress, Dict[int, List[Fragment]]] = defaultdict(lambda: defaultdict(list))

    def feed(self, incoming: Fragment) -> Optional[PDU]:
        source = incoming.network_header.source
        dest = incoming.network_header.destination
        protocol = incoming.fragment_header.protocol

        # The sequence minus fragment is same for all fragments in a given PDU
        base_seq = (MeshProtocol.WindowSize + incoming.fragment_header.sequence -
                    incoming.fragment_header.fragment) % MeshProtocol.WindowSize

        # Buffer the incoming frame and see if it completes a whole segment
        self.buffer[source][base_seq].append(incoming)

        fragments = self.buffer[source][base_seq]
        sorted_fragments = sorted(fragments, key=operator.attrgetter("fragment_header.fragment"))
        has_more = sorted_fragments[-1].fragment_header.flags & Flags.FRAGMENT
        have_all = len(fragments) == sorted_fragments[-1].fragment_header.fragment + 1
        if not has_more and have_all:
            del self.buffer[source][base_seq]
            joined = BytesIO()
            for fragment in sorted_fragments:
                if fragment.network_header.source == source and \
                        fragment.network_header.destination == dest and \
                        fragment.fragment_header.protocol == protocol:
                    joined.write(fragment.payload)
                else:
                    # Fragment consistency error
                    raise RuntimeError("Fragment consistency error, header fields do not match")
            # Parse payload
            if protocol == Protocol.DATAGRAM:
                joined.seek(0)
                datagram_header = DatagramHeader.decode(joined)
                payload = joined.read()
                return Datagram(incoming.network_header, datagram_header, payload)
            else:
                joined.seek(0)
                payload = joined.read()
                return Raw(incoming.network_header, payload)
        else:
            return None


class PacketCodec:
    # TODO make this pluggable for other L3 and L4 protocols
    def __init__(self):
        self.assembler = FragmentAssembler()

    def decode_header(self, stream: BytesIO) -> PacketHeader:
        return PacketHeader.decode(stream)

    def decode_packet(self, header: PacketHeader, stream: BytesIO) -> Optional[PDU]:
        if header.protocol == Protocol.ANNOUNCE:
            return Announce(header, stream.read())
        elif header.protocol == Protocol.FRAGMENT:
            fragment_header = FragmentHeader.decode(stream)
            fragment = Fragment(header, fragment_header, stream.read())
            return self.assembler.feed(fragment)
        elif header.protocol == Protocol.DATAGRAM:
            datagram_header = DatagramHeader.decode(stream)
            payload = stream.read()
            return Datagram(header, datagram_header, payload)
        else:
            payload = stream.read()
            return Raw(header, payload)

    def encode_packet(self, header: PacketHeader, next_header: Optional[Header], payload: bytes) -> bytes:
        stream = BytesIO()
        header.encode(stream)
        if next_header is not None:
            next_header.encode(stream)
        stream.write(payload)
        stream.seek(0)
        return stream.read()


class TTLCache:
    def __init__(self, time: Time, expiry_ms: int):
        self.time = time
        self.expiry_ms = expiry_ms
        self.cache: Set[Any] = set()
        self.seen: List[Tuple[int, Any]] = list()

    def contains(self, header: PacketHeader) -> bool:
        """Check if a packet header has been seen before"""
        now = self.time.time()
        removed = []
        for i, (t, item) in zip(range(len(self.seen)), self.seen):
            if t > now:
                removed.append(i)
                self.cache.remove(item)
        for i in removed:
            del self.seen[i]

        if header in self.cache:
            return True
        else:
            self.cache.add(header)
            self.seen.append((now, header))
            return False


class MeshProtocol(L3Protocol, LoggingMixin):
    """
    A simple network protocol for partially connected meshes.

    When receiving a packet, check if it is the next expected packet from the sender. If not,
    buffer it until we see the next expected packet. The first time we see a packet, retransmit
    it with the FLOOD flag set and TTL decreased by one.

    When transmitting an original packet, attach our send sequence and then increment it. Set
    the TTL to be at least the network width.

    TODO need a way to request some packets to be resent if we buffer for too long
    TODO need a way to recover from sequence errors
    TODO just one port for datagram? like some streaming protocols
    """

    ProtocolId = 0xB0
    WindowSize = 1024
    MaxFragments = 8
    HeaderBytes = 10
    BroadcastAddress = MeshAddress(0xFFFF)

    def __init__(self,
                 time: Time,
                 our_address: MeshAddress,
                 link_multiplexer: LinkMultiplexer,
                 scheduler: Scheduler):
        LoggingMixin.__init__(self)
        self.time = time
        self.link_multiplexer = link_multiplexer
        self.our_address = our_address
        self.announce_timer = scheduler.timer(60000.0, self.announce_self, auto_start=False)

        # TTL cache of seen frames from each source
        self.header_cache: TTLCache = TTLCache(time, 30_000)

        # Packet re-assembler
        self.assembler = FragmentAssembler()
        self.parser = PacketCodec()

        # Our own send sequence
        self.send_seq: int = 1

        self.neighbors: Dict[MeshAddress, datetime] = dict()
        self.datagram_manager: Optional = None
        self.announce_self()

    def can_handle(self, protocol: int) -> bool:
        return protocol == MeshProtocol.ProtocolId

    def handle_l2_payload(self, payload: L2Payload):
        stream = BytesIO(payload.l3_data)
        header = self.parser.decode_header(stream)

        if self.header_cache.contains(header):
            return

        self.debug(f"Incoming packet {header}")

        if header.destination == self.our_address:
            self.handle_local_packet(header, stream)
            return

        if header.destination == self.BroadcastAddress:
            # handle it locally and forward
            self.handle_local_packet(header, stream)

        if header.ttl > 1:
            # Decrease the TTL, clear the origin flag
            header_copy = dataclasses.replace(header, ttl=header.ttl - 1)
            stream.seek(0)
            header_copy.encode(stream)
            stream.seek(0)
            self.broadcast(header_copy, stream.read(), exclude_link_id=payload.link_id)

    def handle_local_packet(self, header: PacketHeader, stream: BytesIO):
        payload = self.parser.decode_packet(header, stream)
        if payload is not None:
            self.debug(f"Handling {payload}")
            if isinstance(payload, Datagram) and self.datagram_manager is not None:
                self.datagram_manager.handle_datagram(payload)
            elif isinstance(payload, Announce):
                announce = cast(Announce, payload)
                self.neighbors[announce.network_header.source] = self.time.datetime()
            else:
                self.warning(f"Unhandled payload {payload}")

    def broadcast(self, header: PacketHeader, buffer: bytes, exclude_link_id: Optional[int] = None):
        """Broadcast a packet to all available L2 links, optionally excluding the link
        this packet was heard on.

        :param header the header of the packet to broadcast
        :param buffer the entire buffer of the packet to broadcast
        :param exclude_link_id an L2 link to exclude from the broadcast
        """
        if exclude_link_id is not None:
            exclude_device = self.link_multiplexer.get_link(exclude_link_id).get_device_id()
        else:
            exclude_device = None

        # Map protocol QoS to internal QoS
        if header.qos == 2:
            qos = QoS.Higher
        elif header.qos == 3:
            qos = QoS.Lower
        else:
            qos = QoS.Default

        for device_id, l2_device in self.link_multiplexer.get_registered_devices().items():
            link_id = l2_device.maybe_open_link(AX25Address("TAPRN"))
            if exclude_device is not None and l2_device.get_device_id() == exclude_device:
                continue

            msg = L3Payload(
                source=header.source,
                destination=header.destination,
                protocol=MeshProtocol.ProtocolId,
                buffer=buffer,
                link_id=link_id,
                qos=qos,
                reliable=False)
            self.debug(f"Forwarding {msg}")
            self.send_packet(msg)

    def announce_self(self):
        self.info("Sending announce")
        network_header = PacketHeader(
            version=0,
            qos=QoS.Default,
            protocol=Protocol.ANNOUNCE,
            ttl=7,
            identity=(self.send_seq % MeshProtocol.WindowSize),
            length=0,
            source=self.our_address,
            destination=self.BroadcastAddress,
        )
        buffer = self.parser.encode_packet(network_header, None, b"Hello, World")
        self.broadcast(network_header, buffer)
        self.send_seq += 1
        self.announce_timer.reset()

    def send_datagram(self, destination: MeshAddress, datagram_header: DatagramHeader, data: bytes):
        network_header = PacketHeader(
            version=0,
            qos=QoS.Default,
            protocol=Protocol.DATAGRAM,
            ttl=7,
            identity=(self.send_seq % MeshProtocol.WindowSize),
            length=len(data),
            source=self.our_address,
            destination=destination,
        )
        buffer = self.parser.encode_packet(network_header, datagram_header, data)
        self.broadcast(network_header, buffer)
        self.send_seq += 1

    def _max_fragment_size(self):
        # We want uniform packets, so get the min L2 MTU
        l2s = self.link_multiplexer.get_registered_devices().values()
        l2_mtu = min([l2.maximum_transmission_unit() for l2 in l2s])
        return l2_mtu - MeshProtocol.HeaderBytes

    def route_packet(self, address: L3Address) -> Tuple[bool, int]:
        # Subtract L3 header size and multiply by max fragments
        l3_mtu = MeshProtocol.MaxFragments * (self._max_fragment_size() - MeshProtocol.HeaderBytes)
        return True, l3_mtu

    def send_packet(self, payload: L3Payload) -> bool:
        return self.link_multiplexer.get_queue(payload.link_id).offer(payload)

    def listen(self, address: MeshAddress):
        # By default we listen for all addresses
        pass

    def register_transport_protocol(self, protocol: L4Protocol) -> None:
        self.datagram_manager = protocol
