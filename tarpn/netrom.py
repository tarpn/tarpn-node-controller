from dataclasses import dataclass
from enum import IntFlag
from typing import cast, Dict

from tarpn.app import Application
from tarpn.ax25 import AX25Call, parse_ax25_call, AX25Packet, UIFrame, L3Protocol
from tarpn.frame import L3Handler


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
    origin: AX25Call
    dest: AX25Call
    ttl: int
    circuit_idx: int
    circuit_id: int
    tx_seq_num: int
    rx_seq_num: int
    op_byte: int

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


@dataclass
class NetRomConnectAck(NetRomPacket):
    accept_window_size: int


@dataclass
class NetRomInfo(NetRomPacket):
    info: bytes


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
        return NetRomConnectRequest(origin, dest, ttl, circuit_idx, circuit_id, tx_seq_num, rx_seq_num, op_byte,
                                    proposed_window_size, origin_user, origin_node)
    elif op_type == OpType.ConnectAcknowledge:
        accept_window_size = next(bytes_iter)
        return NetRomConnectAck(origin, dest, ttl, circuit_idx, circuit_id, tx_seq_num, rx_seq_num, op_byte,
                                accept_window_size)
    elif op_type == OpType.Information:
        info = bytes(bytes_iter)
        return NetRomInfo(origin, dest, ttl, circuit_idx, circuit_id, tx_seq_num, rx_seq_num, op_byte, info)
    elif op_type in (OpType.InformationAcknowledge, OpType.DisconnectRequest, OpType.DisconnectAcknowledge):
        return NetRomPacket(origin, dest, ttl, circuit_idx, circuit_id, tx_seq_num, rx_seq_num, op_byte)


class NetRomHandler(L3Handler):

    def __init__(self):
        self.l3_apps: Dict[AX25Call, Application] = {}

    def maybe_handle_special(self, packet: AX25Packet) -> bool:
        if type(packet) == UIFrame:
            ui = cast(UIFrame, packet)
            if ui.protocol == L3Protocol.NetRom and ui.dest == AX25Call("NODES"):
                # Parse this NODES packet and mark it as handled
                print("Got NODES")
                return True
        return False

    def handle(self, data: bytes):
        netrom_packet = parse_netrom_packet(data)
        print(f"NET/ROM: {netrom_packet}")
        # If packet is for us, handle it, otherwise route it
        if netrom_packet.dest in self.l3_apps.keys():
            # TODO pass to netrom state machine
            pass
        else:
            # TODO route this towards its destination
            pass

