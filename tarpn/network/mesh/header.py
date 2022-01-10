import datetime
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from io import BytesIO
from typing import List

from tarpn.network.mesh import MeshAddress
from tarpn.util import ByteUtils


class Protocol(IntEnum):
    # up to 0x0F
    NONE = 0x00
    CONTROL = 0x01
    HELLO = 0x02
    LINK_STATE = 0x03
    LINK_STATE_QUERY = 0x04

    RECORD = 0x05

    DATAGRAM = 0x06
    FRAGMENT = 0x07
    RELIABLE = 0x08
    BROADCAST = 0x09

    def __str__(self):
        return self.name

    def __repr__(self):
        return self.name


@dataclass(eq=True, frozen=True)
class Header:
    def encode(self, data: BytesIO) -> None:
        raise NotImplementedError()

    def size(self) -> int:
        raise NotImplementedError()


@dataclass(eq=True, frozen=True)
class NetworkHeader(Header):
    version: int
    qos: int
    protocol: Protocol
    ttl: int
    identity: int
    length: int
    source: MeshAddress
    destination: MeshAddress

    def id(self):
        return self.source, self.destination, self.identity

    def __hash__(self):
        return hash(self.id())

    @classmethod
    def size(cls):
        return 10

    @classmethod
    def decode(cls, data: BytesIO):
        byte = ByteUtils.read_int8(data)
        version = ByteUtils.hi_bits(byte, 4)
        protocol = ByteUtils.lo_bits(byte, 4)
        byte = ByteUtils.read_int8(data)
        qos = ByteUtils.hi_bits(byte, 3)
        ttl = ByteUtils.lo_bits(byte, 5)
        identity = ByteUtils.read_uint16(data)
        length = ByteUtils.read_uint16(data)
        source = ByteUtils.read_uint16(data)
        dest = ByteUtils.read_uint16(data)
        return cls(version=version,
                   protocol=Protocol(protocol),
                   qos=qos,
                   ttl=ttl,
                   identity=identity,
                   length=length,
                   source=MeshAddress(source),
                   destination=MeshAddress(dest))

    def encode(self, data: BytesIO):
        ByteUtils.write_hi_lo(data, self.version, self.protocol, 4)
        ByteUtils.write_hi_lo(data, self.qos, self.ttl, 3)
        ByteUtils.write_uint16(data, self.identity)
        ByteUtils.write_uint16(data, self.length)
        ByteUtils.write_uint16(data, self.source.id)
        ByteUtils.write_uint16(data, self.destination.id)


class ControlType(IntEnum):
    # Up to 0x7F
    PING = 0x00
    LOOKUP = 0x01
    UNREACHABLE = 0x02

    def _missing_(cls, value):
        return None

    def __str__(self):
        return f"{self.name} ({self.value:02x})"


@dataclass(eq=True, frozen=True)
class ControlHeader(Header):
    is_request: bool
    control_type: ControlType
    extra_length: int
    extra: bytes

    def size(self) -> int:
        return 2 + self.extra_length

    @classmethod
    def decode(cls, data: BytesIO):
        b = ByteUtils.read_uint8(data)
        is_request = bool(ByteUtils.hi_bits(b, 1))
        control_type = ControlType(ByteUtils.lo_bits(b, 7))
        extra_length = ByteUtils.read_uint8(data)
        extra = data.read(extra_length)
        return cls(is_request=is_request, control_type=control_type, extra_length=extra_length, extra=extra)

    def encode(self, data: BytesIO):
        ByteUtils.write_hi_lo(data, int(self.is_request), self.control_type.value, 1)
        ByteUtils.write_uint8(data, self.extra_length)
        data.write(self.extra)

    def __str__(self):
        return f"CTRL {str(self.control_type)} req={self.is_request}"


@dataclass(eq=True, frozen=True)
class HelloHeader(Header):
    name: str
    neighbors: List[MeshAddress]

    def size(self) -> int:
        return len(self.name) + 1 + 2 * len(self.neighbors)

    @classmethod
    def decode(cls, data: BytesIO):
        name = ByteUtils.read_utf8(data)
        count = ByteUtils.read_uint8(data)
        neighbors = []
        for _ in range(count):
            neighbors.append(MeshAddress(ByteUtils.read_uint16(data)))
        return cls(name=name, neighbors=neighbors)

    def encode(self, data: BytesIO):
        ByteUtils.write_utf8(data, self.name)
        ByteUtils.write_uint8(data, len(self.neighbors))
        for neighbor in self.neighbors:
            ByteUtils.write_uint16(data, neighbor.id)

    def __str__(self):
        return f"HELLO {self.name} {self.neighbors}"


@dataclass(eq=True, frozen=True)
class LinkStateHeader(Header):
    node: MeshAddress
    via: MeshAddress
    quality: int
    created: datetime.datetime = field(default_factory=datetime.datetime.utcnow)

    @classmethod
    def size(cls) -> int:
        return 5

    @classmethod
    def decode(cls, data: BytesIO):
        node = MeshAddress(ByteUtils.read_uint16(data))
        via = MeshAddress(ByteUtils.read_uint16(data))
        quality = ByteUtils.read_uint8(data)
        return cls(node=node, via=via, quality=quality)

    def encode(self, data: BytesIO):
        ByteUtils.write_uint16(data, self.node.id)
        ByteUtils.write_uint16(data, self.via.id)
        ByteUtils.write_uint8(data, self.quality)

    def __repr__(self):
        return f"(node={self.node} via={self.via} q={self.quality})"


@dataclass(eq=True, frozen=True)
class LinkStateAdvertisementHeader(Header):
    node: MeshAddress
    name: str
    epoch: int
    link_states: List[LinkStateHeader]

    def size(self) -> int:
        return 4 + len(self.name.encode("utf-8")) + sum([link_state.size() for link_state in self.link_states])

    @classmethod
    def decode(cls, data: BytesIO):
        node = MeshAddress(ByteUtils.read_uint16(data))
        name = ByteUtils.read_utf8(data)
        epoch = ByteUtils.read_int8(data)
        count = ByteUtils.read_uint8(data)
        link_states = []
        for _ in range(count):
            link_states.append(LinkStateHeader.decode(data))
        return cls(
            node=node,
            name = name,
            epoch=epoch,
            link_states=link_states
        )

    def encode(self, data: BytesIO):
        ByteUtils.write_uint16(data, self.node.id)
        ByteUtils.write_utf8(data, self.name)
        ByteUtils.write_int8(data, self.epoch)
        ByteUtils.write_uint8(data, len(self.link_states))
        for link_state in self.link_states:
            link_state.encode(data)

    def __str__(self):
        return f"ADVERT {self.node} epoch={self.epoch} links={self.link_states}"


@dataclass(eq=True, frozen=True)
class LinkStateQueryHeader(Header):
    node: MeshAddress
    epoch: int
    link_nodes: List[MeshAddress]
    link_epochs: List[int]

    def size(self) -> int:
        return 4 + 3 * len(self.link_nodes)

    @classmethod
    def decode(cls, data: BytesIO):
        node = MeshAddress(ByteUtils.read_uint16(data))
        epoch = ByteUtils.read_int8(data)
        count = ByteUtils.read_uint8(data)
        link_nodes = []
        link_epochs = []
        for _ in range(count):
            link_nodes.append(MeshAddress(ByteUtils.read_uint16(data)))
            link_epochs.append(ByteUtils.read_int8(data))
        return cls(node=node, epoch=epoch, link_nodes=link_nodes, link_epochs=link_epochs)

    def encode(self, data: BytesIO):
        ByteUtils.write_uint16(data, self.node.id)
        ByteUtils.write_int8(data, self.epoch)
        ByteUtils.write_uint8(data, len(self.link_nodes))
        for node, epoch in zip(self.link_nodes, self.link_epochs):
            ByteUtils.write_uint16(data, node.id)
            ByteUtils.write_int8(data, epoch)

    def __str__(self):
        return f"QUERY {self.node} {self.epoch}"


class RecordType(IntEnum):
    # up to 0xFF
    NAME = 0x01


@dataclass(eq=True, frozen=True)
class RecordHeader(Header):
    record_type: RecordType
    length: int
    value: bytes

    @classmethod
    def decode(cls, data: BytesIO):
        record_type = RecordType(ByteUtils.read_uint8(data))
        length = ByteUtils.read_uint8(data)
        value = data.read(length)
        return cls(record_type=record_type, length=length, value=value)

    def encode(self, data: BytesIO) -> None:
        ByteUtils.write_uint8(data, self.record_type.value)
        ByteUtils.write_uint8(data, self.length)
        data.write(self.value)

    def size(self) -> int:
        return 2 + self.length


class FragmentFlags(IntFlag):
    NONE = 0b0000
    FRAGMENT = 0b0001


@dataclass(eq=True, frozen=True)
class FragmentHeader(Header):
    protocol: Protocol
    flags: FragmentFlags
    fragment: int
    sequence: int

    @classmethod
    def size(cls) -> int:
        return 4

    @classmethod
    def decode(cls, data: BytesIO):
        byte = ByteUtils.read_int8(data)
        protocol = ByteUtils.hi_bits(byte, 4)
        flags = ByteUtils.lo_bits(byte, 4)
        fragment = ByteUtils.read_uint8(data)
        sequence = ByteUtils.read_uint16(data)
        return cls(protocol=Protocol(protocol),
                   flags=FragmentFlags(flags),
                   fragment=fragment,
                   sequence=sequence)

    def encode(self, data: BytesIO):
        ByteUtils.write_hi_lo(data, self.protocol, self.flags, 4)
        ByteUtils.write_uint8(data, self.fragment)
        ByteUtils.write_uint16(data, self.sequence)


class ReliableFlags(IntFlag):
    NONE = 0b0000
    ACK = 0b0001


@dataclass(eq=True, frozen=True)
class ReliableHeader(Header):
    protocol: Protocol
    flags: ReliableFlags
    sequence: int

    @classmethod
    def size(cls) -> int:
        return 3

    @classmethod
    def decode(cls, data: BytesIO):
        byte = ByteUtils.read_int8(data)
        protocol = ByteUtils.hi_bits(byte, 4)
        flags = ReliableFlags(ByteUtils.lo_bits(byte, 4))
        seq = ByteUtils.read_uint16(data)
        return cls(protocol=protocol, flags=flags, sequence=seq)

    def encode(self, data: BytesIO):
        ByteUtils.write_hi_lo(data, self.protocol, self.flags, 4)
        ByteUtils.write_uint16(data, self.sequence)


@dataclass(eq=True, frozen=True)
class BroadcastHeader(Header):
    source: MeshAddress
    port: int
    sequence: int
    length: int
    checksum: int

    @classmethod
    def size(cls) -> int:
        return 9

    @classmethod
    def decode(cls, data: BytesIO):
        source = ByteUtils.read_uint16(data)
        port = ByteUtils.read_uint8(data)
        seq = ByteUtils.read_uint16(data)
        length = ByteUtils.read_uint16(data)
        checksum = ByteUtils.read_uint16(data)

        return cls(
            source=MeshAddress(source),
            port=port,
            sequence=seq,
            length=length,
            checksum=checksum
        )

    def encode(self, data: BytesIO):
        ByteUtils.write_uint16(data, self.source.id)
        ByteUtils.write_uint8(data, self.port)
        ByteUtils.write_uint16(data, self.sequence)
        ByteUtils.write_uint16(data, self.length)
        ByteUtils.write_uint16(data, self.checksum)


@dataclass(eq=True, frozen=True)
class DatagramHeader(Header):
    source: int
    destination: int
    length: int
    checksum: int

    @classmethod
    def size(cls) -> int:
        return 6

    @classmethod
    def decode(cls, data: BytesIO):
        source = ByteUtils.read_uint8(data)
        dest = ByteUtils.read_uint8(data)
        length = ByteUtils.read_uint16(data)
        checksum = ByteUtils.read_uint16(data)
        return cls(source=source,
                   destination=dest,
                   length=length,
                   checksum=checksum)

    def encode(self, data: BytesIO):
        ByteUtils.write_uint8(data, self.source)
        ByteUtils.write_uint8(data, self.destination)
        ByteUtils.write_uint16(data, self.length)
        ByteUtils.write_uint16(data, self.checksum)


@dataclass(eq=True, frozen=True)
class Fragment:
    network_header: NetworkHeader
    fragment_header: FragmentHeader
    payload: bytes


@dataclass(eq=True, frozen=True)
class PDU:
    network_header: NetworkHeader


@dataclass(eq=True, frozen=True)
class Announce(PDU):
    payload: bytes


@dataclass(eq=True, frozen=True)
class Datagram(PDU):
    datagram_header: DatagramHeader
    payload: bytes


@dataclass(eq=True, frozen=True)
class Broadcast(PDU):
    broadcast_header: BroadcastHeader
    payload: bytes


@dataclass(eq=True, frozen=True)
class Raw(PDU):
    payload: bytes
