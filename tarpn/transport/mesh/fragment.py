import operator
import secrets
import struct
import threading
from collections import defaultdict
from io import BytesIO
from typing import Dict, List

from tarpn.log import LoggingMixin
from tarpn.network import L3Protocol, QoS
from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.header import Fragment, DatagramHeader, FragmentHeader, FragmentFlags, Protocol, NetworkHeader, \
    Datagram
from tarpn.network.mesh.protocol import MeshProtocol, encode_packet
from tarpn.transport import L4Protocol
from tarpn.transport.mesh import L4Handler
from tarpn.util import chunks


class FragmentProtocol(L4Handler, LoggingMixin):
    # TODO expire fragments after some time

    def __init__(self, network: L3Protocol, datagram_manager: L4Protocol):
        LoggingMixin.__init__(self)
        self.network = network
        self.datagram_manager = datagram_manager

        self.send_seq: int = 1
        self.seq_lock = threading.Lock()

        # A buffer of undelivered fragment. Structured like source -> sequence -> fragments
        self.buffer: Dict[MeshAddress, Dict[int, List[Fragment]]] = defaultdict(lambda: defaultdict(list))

    def fragment_datagram(self, source: MeshAddress, destination: MeshAddress,
                          datagram_header: DatagramHeader, data: bytes):
        stream = BytesIO()
        datagram_header.encode(stream)
        stream.write(data)
        stream.seek(0)
        fragment_size = self.network.mtu() - FragmentHeader.size()
        fragments = list(chunks(stream.read(), fragment_size))
        with self.send_seq:
            sequences = range(self.send_seq, len(fragments))
            self.send_seq += len(fragments)

        for i, fragment in zip(range(len(fragments)), fragments):
            if i < len(fragments) - 1:
                flags = FragmentFlags.FRAGMENT
            else:
                flags = FragmentFlags.NONE
            fragment_header = FragmentHeader(
                protocol=Protocol.DATAGRAM,
                flags=flags,
                fragment=i,
                sequence=sequences[i]
            )
            network_header = NetworkHeader(
                version=0,
                qos=QoS.Default,
                protocol=Protocol.FRAGMENT,
                ttl=MeshProtocol.DefaultTTL,
                identity=struct.unpack('<H', secrets.token_bytes(2))[0],
                length=len(fragment),
                source=source,
                destination=destination,
            )
            buffer = encode_packet(network_header, [fragment_header], fragment)
            self.network.send(network_header, buffer)

    def handle_l4(self, network_header: NetworkHeader, stream: BytesIO):
        fragment_header = FragmentHeader.decode(stream)
        fragment = Fragment(network_header, fragment_header, stream.read())

        source = fragment.network_header.source
        dest = fragment.network_header.destination
        protocol = fragment.fragment_header.protocol

        # Only Datagram allowed inside fragments (for now)
        if protocol != Protocol.DATAGRAM:
            self.warning(f"Dropping fragment for unsupport protocol {protocol}")

        # The sequence minus fragment is same for all fragments in a given PDU
        base_seq = (MeshProtocol.WindowSize + fragment.fragment_header.sequence -
                    fragment.fragment_header.fragment) % MeshProtocol.WindowSize

        # Buffer the incoming frame and see if it completes a whole segment
        self.buffer[source][base_seq].append(fragment)

        fragments = self.buffer[source][base_seq]
        sorted_fragments = sorted(fragments, key=operator.attrgetter("fragment_header.fragment"))
        has_more = sorted_fragments[-1].fragment_header.flags & FragmentFlags.FRAGMENT
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
            joined.seek(0)
            datagram_header = DatagramHeader.decode(joined)
            payload = joined.read()
            self.datagram_manager.handle_datagram(
                Datagram(fragment.network_header, datagram_header, payload)
            )