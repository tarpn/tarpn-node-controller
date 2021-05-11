from dataclasses import dataclass
from enum import IntEnum, IntFlag
from io import BytesIO

from tarpn.network.mesh import MeshAddress
from tarpn.util import ByteUtils


class Protocol(IntEnum):
    ANNOUNCE = 0x01
    DATAGRAM = 0x02
    FRAGMENT = 0x03

    def __repr__(self):
        return self.name


class Flags(IntFlag):
    NONE = 0b0000
    FRAGMENT = 0b0001
    ORIGIN = 0b0010


@dataclass(eq=True, frozen=True)
class Header:
    def encode(self, data: BytesIO):
        raise NotImplementedError()


@dataclass(eq=True, frozen=True)
class PacketHeader(Header):
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


@dataclass(eq=True, frozen=True)
class FragmentHeader(Header):
    protocol: Protocol
    flags: Flags
    fragment: int
    sequence: int

    @classmethod
    def decode(cls, data: BytesIO):
        byte = ByteUtils.read_int8(data)
        protocol = ByteUtils.hi_bits(byte, 4)
        flags = ByteUtils.lo_bits(byte, 4)
        fragment = ByteUtils.read_uint8(data)
        sequence = ByteUtils.read_uint16(data)
        return cls(protocol=Protocol(protocol),
                   flags=Flags(flags),
                   fragment=fragment,
                   sequence=sequence)

    @staticmethod
    def size() -> int:
        return 4

    def encode(self, data: BytesIO):
        ByteUtils.write_hi_lo(data, self.protocol, self.flags, 4)
        ByteUtils.write_uint8(data, self.fragment)
        ByteUtils.write_uint16(data, self.sequence)


@dataclass(eq=True, frozen=True)
class DatagramHeader(Header):
    source: int
    destination: int
    length: int
    checksum: int

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

    @staticmethod
    def size() -> int:
        return 6

    def encode(self, data: BytesIO):
        ByteUtils.write_uint8(data, self.source)
        ByteUtils.write_uint8(data, self.destination)
        ByteUtils.write_uint16(data, self.length)
        ByteUtils.write_uint16(data, self.checksum)


@dataclass(eq=True, frozen=True)
class Fragment:
    network_header: PacketHeader
    fragment_header: FragmentHeader
    payload: bytes


@dataclass(eq=True, frozen=True)
class PDU:
    network_header: PacketHeader


@dataclass(eq=True, frozen=True)
class Announce(PDU):
    payload: bytes


@dataclass(eq=True, frozen=True)
class Datagram(PDU):
    datagram_header: DatagramHeader
    payload: bytes


@dataclass(eq=True, frozen=True)
class Raw(PDU):
    payload: bytes
