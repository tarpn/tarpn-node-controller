from dataclasses import dataclass
from enum import IntFlag

from tarpn.ax25 import AX25Call, parse_ax25_call, L3Protocol


class OpType(IntFlag):
    Unknown = 0x00
    ConnectRequest = 0x01
    ConnectAcknowledge = 0x02
    DisconnectRequest = 0x03,
    DisconnectAcknowledge = 0x04
    Information = 0x05
    InformationAcknowledge = 0x06

    def as_op_byte(self, choke: bool, nak: bool, more_follows: bool):
        """Encode the flags with the opcode into the op byte"""
        return self | (int(choke) << 7) | (int(nak) << 6) | (int(more_follows) << 5)

    @classmethod
    def create(cls, op_byte: int):
        masked = op_byte & 0x0F
        if masked in OpType.__members__.values():
            return cls(masked)
        else:
            return OpType.Unknown


@dataclass
class NetRomPacket:
    dest: AX25Call
    origin: AX25Call
    ttl: int
    circuit_idx: int
    circuit_id: int
    tx_seq_num: int
    rx_seq_num: int
    op_byte: int

    @classmethod
    def dummy(cls, dest: AX25Call, origin: AX25Call):
        return NetRomPacket(dest, origin, 0, 0, 0, 0, 0, 0)

    @property
    def buffer(self) -> bytes:
        b = bytearray()
        self.origin.write(b)
        self.dest.write(b)
        b.append(self.ttl)
        b.append(self.circuit_idx)
        b.append(self.circuit_id)
        b.append(self.tx_seq_num)
        b.append(self.rx_seq_num)
        b.append(self.op_byte)
        return bytes(b)

    def op_type(self):
        return OpType.create(self.op_byte)

    def choke(self):
        return (self.op_byte & 0x80) == 0x80

    def nak(self):
        return (self.op_byte & 0x40) == 0x40

    def more_follows(self):
        return (self.op_byte & 0x20) == 0x20


@dataclass
class NetRomConnectRequest(NetRomPacket):
    proposed_window_size: int
    origin_user: AX25Call
    origin_node: AX25Call

    @property
    def buffer(self) -> bytes:
        b = bytearray()
        self.origin.write(b)
        self.dest.write(b)
        b.append(self.ttl)
        b.append(self.circuit_idx)
        b.append(self.circuit_id)
        b.append(self.tx_seq_num)
        b.append(self.rx_seq_num)
        b.append(self.op_byte)
        b.append(self.proposed_window_size)
        self.origin_user.write(b)
        self.origin_node.write(b)
        return bytes(b)


@dataclass
class NetRomConnectAck(NetRomPacket):
    accept_window_size: int

    @property
    def buffer(self) -> bytes:
        b = bytearray()
        self.origin.write(b)
        self.dest.write(b)
        b.append(self.ttl)
        b.append(self.circuit_idx)
        b.append(self.circuit_id)
        b.append(self.tx_seq_num)
        b.append(self.rx_seq_num)
        b.append(self.op_byte)
        b.append(self.accept_window_size)
        return bytes(b)


@dataclass
class NetRomInfo(NetRomPacket):
    info: bytes

    @property
    def buffer(self) -> bytes:
        b = bytearray()
        self.origin.write(b)
        self.dest.write(b)
        b.append(self.ttl)
        b.append(self.circuit_idx)
        b.append(self.circuit_id)
        b.append(self.tx_seq_num)
        b.append(self.rx_seq_num)
        b.append(self.op_byte)
        b.extend(self.info)
        return bytes(b)


def parse_netrom_packet(data: bytes):
    bytes_iter = iter(data)
    origin = parse_ax25_call(bytes_iter)
    dest = parse_ax25_call(bytes_iter)

    ttl = next(bytes_iter)
    circuit_idx = next(bytes_iter)
    circuit_id = next(bytes_iter)
    tx_seq_num = next(bytes_iter)
    rx_seq_num = next(bytes_iter)
    op_byte = next(bytes_iter)
    op_type = OpType.create(op_byte)

    if op_type == OpType.ConnectRequest:
        proposed_window_size = next(bytes_iter)
        origin_user = parse_ax25_call(bytes_iter)
        origin_node = parse_ax25_call(bytes_iter)
        return NetRomConnectRequest(dest, origin, ttl, circuit_idx, circuit_id, tx_seq_num, rx_seq_num, op_byte,
                                    proposed_window_size, origin_user, origin_node)
    elif op_type == OpType.ConnectAcknowledge:
        accept_window_size = next(bytes_iter)
        return NetRomConnectAck(dest, origin, ttl, circuit_idx, circuit_id, tx_seq_num, rx_seq_num, op_byte,
                                accept_window_size)
    elif op_type == OpType.Information:
        info = bytes(bytes_iter)
        return NetRomInfo(dest, origin, ttl, circuit_idx, circuit_id, tx_seq_num, rx_seq_num, op_byte, info)
    elif op_type in (OpType.InformationAcknowledge, OpType.DisconnectRequest, OpType.DisconnectAcknowledge):
        return NetRomPacket(dest, origin, ttl, circuit_idx, circuit_id, tx_seq_num, rx_seq_num, op_byte)


class NetRom:
    def nl_data(self, remote_call: AX25Call, local_call: AX25Call, protocol: L3Protocol, data: bytes) -> None:
        raise NotImplemented

    def nl_connect(self, remote_call: AX25Call, local_call: AX25Call) -> None:
        raise NotImplemented

    def nl_disconnect(self, remote_call: AX25Call, local_call: AX25Call) -> None:
        raise NotImplemented

    def write_packet(self, packet: NetRomPacket) -> bool:
        raise NotImplemented
