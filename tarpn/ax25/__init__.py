from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Iterator


@dataclass
class AX25Call:
    callsign: str = field(default="NOCALL")
    ssid: int = field(default=0)
    rr: int = field(default=0, compare=False)
    c_flag: bool = field(default=False, compare=False)
    last: bool = field(default=False, compare=False)

    def __post_init__(self):
        self.callsign = self.callsign.upper()

    def __repr__(self):
        return f"{self.callsign}-{self.ssid}"

    def __hash__(self):
        return repr(self).__hash__()

    def clear_flags(self):
        self.rr = 0
        self.c_flag = False
        self.last = False

    def write(self, buffer: bytearray):
        padded_call = self.callsign.ljust(6)
        for c in padded_call.encode("ascii"):
            buffer.append((c & 0xFF) << 1)
        ssid_byte = ((self.ssid << 1) & 0x1E) | int(self.last)
        if self.c_flag:
            ssid_byte |= 0x80
        ssid_byte |= ((self.rr << 5) & 0x60)
        buffer.append(ssid_byte)

    @classmethod
    def parse(cls, s: str):
        parts = s.split("-")
        if len(parts) != 2:
            raise ValueError(f"Cannot parse callsign {s}. Expected callsign with ssid (e.g., C4LL-9)")
        return cls(parts[0], int(parts[1]))


@dataclass
class AX25Address:
    port: int
    call: AX25Call


@dataclass
class AX25Packet:
    buffer: bytes = field(repr=False)
    dest: AX25Call
    source: AX25Call
    repeaters: List[AX25Call]
    control_byte: int

    def get_command(self):
        if self.dest.c_flag:
            if self.source.c_flag:
                return SupervisoryCommand.Legacy
            else:
                return SupervisoryCommand.Command
        else:
            if self.source.c_flag:
                return SupervisoryCommand.Response
            else:
                return SupervisoryCommand.Legacy


class SupervisoryType(IntEnum):
    RR = 0x01
    """Receiver Ready"""
    RNR = 0x05
    """Receiver Not Ready"""
    REJ = 0x09
    """Rejected"""

    @staticmethod
    def from_control_byte(b):
        for name, member in SupervisoryType.__members__.items():
            if (b & member.value) == member.value:
                return member
        raise ValueError(f"No such SupervisoryType {hex(b)}")

    def to_byte(self, nr: int, poll_final: bool):
        return int(self) | (int(poll_final) << 4) | ((nr << 5) & 0xE0)


class SupervisoryCommand(IntEnum):
    Legacy = 0
    Command = 1
    Response = 2

    def update_calls(self, dest: AX25Call, source: AX25Call):
        if self == SupervisoryCommand.Command:
            dest.c_flag = True
            source.c_flag = False
        elif self == SupervisoryCommand.Response:
            dest.c_flag = False
            source.c_flag = True
        else:
            # Legacy, don't use c bit
            pass


@dataclass
class SFrame(AX25Packet):
    poll_final: bool
    receive_seq_number: int
    control_type: SupervisoryType

    @classmethod
    def s_frame(cls,
                dest: AX25Call,
                source: AX25Call,
                repeaters: List[AX25Call],
                command: SupervisoryCommand,
                control_type: SupervisoryType,
                receive_seq_number: int,
                poll_final: bool):
        buffer = bytearray()
        dest.clear_flags()
        source.clear_flags()
        source.last = True
        command.update_calls(dest, source)

        dest.write(buffer)
        source.write(buffer)
        # TODO repeaters

        control_byte = control_type.to_byte(receive_seq_number, poll_final)
        buffer.append(control_byte)

        return SFrame(bytes(buffer), dest, source, repeaters, control_byte,
                      poll_final, receive_seq_number, control_type)


class UnnumberedType(IntEnum):
    SABM = 0x2F
    """Set Asynchronous Balanced Mode"""
    DISC = 0x43
    """Disconnect"""
    DM = 0x0F
    """Disconnected Mode"""
    UA = 0x63
    """Unnumbered Acknowledge"""
    FRMR = 0x87
    """Frame Reject"""
    UI = 0x03
    """Unnumbered Information"""

    @staticmethod
    def from_control_byte(b):
        b = b & ~0x10
        return UnnumberedType(b)

    def to_byte(self, poll_final: bool):
        return int(self) | (int(poll_final) << 4)


@dataclass
class UFrame(AX25Packet):
    poll_final: bool
    u_type: UnnumberedType

    @staticmethod
    def u_frame(dest: AX25Call,
                source: AX25Call,
                repeaters: List[AX25Call],
                command: SupervisoryCommand,
                u_type: UnnumberedType,
                poll_final: bool):
        buffer = bytearray()
        dest.clear_flags()
        source.clear_flags()
        source.last = True
        command.update_calls(dest, source)

        dest.write(buffer)
        source.write(buffer)
        # TODO repeaters

        control_byte = u_type.to_byte(poll_final)
        buffer.append(control_byte)

        return UFrame(bytes(buffer), dest, source, repeaters, control_byte,
                      poll_final, u_type)


class L3Protocol(IntEnum):
    NoProtocol = 0x00
    SegmentationFragment = 0x08  # TODO Implement this?
    NetRom = 0xCF
    NoLayer3 = 0xF0
    # TODO include others?


@dataclass
class UIFrame(UFrame):
    protocol: L3Protocol
    info: bytes

    @staticmethod
    def ui_frame(dest: AX25Call,
                 source: AX25Call,
                 repeaters: List[AX25Call],
                 command: SupervisoryCommand,
                 poll_final: bool,
                 protocol: L3Protocol,
                 info: bytes):
        buffer = bytearray()
        dest.clear_flags()
        source.clear_flags()
        source.last = True
        command.update_calls(dest, source)

        dest.write(buffer)
        source.write(buffer)
        # TODO repeaters

        control_byte = UnnumberedType.UI.to_byte(poll_final)
        buffer.append(control_byte)
        buffer.append(protocol)
        buffer.extend(info)
        return UIFrame(bytes(buffer), dest, source, repeaters, control_byte,
                       poll_final, UnnumberedType.UI, protocol, info)


@dataclass
class IFrame(AX25Packet):
    poll: bool
    receive_seq_number: int
    send_seq_number: int
    protocol: L3Protocol
    info: bytes

    @staticmethod
    def i_frame(dest: AX25Call,
                source: AX25Call,
                repeaters: List[AX25Call],
                command: SupervisoryCommand,
                poll: bool,
                receive_seq_number: int,
                send_seq_number: int,
                protocol: L3Protocol,
                info: bytes):
        buffer = bytearray()
        dest.clear_flags()
        source.clear_flags()
        source.last = True
        command.update_calls(dest, source)

        dest.write(buffer)
        source.write(buffer)
        # TODO repeaters

        control_byte = ((receive_seq_number << 5) & 0xE0) | ((send_seq_number << 1) & 0x0E) | (int(poll) << 4)
        buffer.append(control_byte)
        buffer.append(protocol)
        buffer.extend(info)
        return IFrame(bytes(buffer), dest, source, repeaters, control_byte,
                      poll, receive_seq_number, send_seq_number, protocol, info)


@dataclass
class InternalInfo(AX25Packet):
    protocol: L3Protocol
    info: bytes

    @classmethod
    def internal_info(cls, protocol: L3Protocol, info: bytes):
        return InternalInfo(bytes(), AX25Call(), AX25Call(), [], 0x00, protocol, info)


@dataclass
class DummyPacket(AX25Packet):
    @classmethod
    def dummy(cls, dest: AX25Call, source: AX25Call):
        return cls(bytes(), dest, source, [], 0x00)


def parse_ax25_call(byte_iter: Iterator[int]):
    call = ""
    for i in range(6):
        c = (next(byte_iter) & 0xFF) >> 1
        if c != " ":
            call += chr(c)

    ssid_byte = next(byte_iter)
    ssid = (ssid_byte & 0x1E) >> 1
    rr = (ssid_byte & 0x60) >> 5
    c_flag = (ssid_byte & 0x80) != 0
    last = (ssid_byte & 0x01) != 0

    return AX25Call(call.strip(), ssid, rr, c_flag, last)


def decode_ax25_packet(buffer: bytes):
    """Decode an AX25 packet from a sequence of bytes.
    The resulting packet can be one of I, U, S, or UI frames.
    """
    byte_iter = iter(buffer)
    dest = parse_ax25_call(byte_iter)
    source = parse_ax25_call(byte_iter)
    repeaters = []
    if not source.last:
        repeater = source
        while not repeater.last:
            repeater = parse_ax25_call(byte_iter)
            repeaters.append(repeater)
    control_byte = next(byte_iter)
    poll_final = (control_byte & 0x10) == 0x10
    if (control_byte & 0x01) == 0:
        # I frame
        pid_byte = next(byte_iter)
        protocol = L3Protocol(pid_byte)
        info = bytes(byte_iter)
        recv_seq = (control_byte >> 5) & 0x07
        send_seq = (control_byte >> 1) & 0x07
        if next(byte_iter, None):
            raise BufferError(f"Underflow exception, did not expect any more bytes here. {str(buffer)}")
        return IFrame(buffer, dest, source, repeaters, control_byte, poll_final, recv_seq, send_seq, protocol, info)
    elif (control_byte & 0x03) == 0x03:
        # U frame
        u_type = UnnumberedType.from_control_byte(control_byte)
        if u_type == UnnumberedType.UI:
            pid_byte = next(byte_iter)
            protocol = L3Protocol(pid_byte)
            info = bytes(byte_iter)
            if next(byte_iter, None):
                raise BufferError(f"Underflow exception, did not expect any more bytes here. {str(buffer)}")
            return UIFrame(buffer, dest, source, repeaters, control_byte, poll_final, u_type, protocol, info)
        else:
            if next(byte_iter, None):
                raise BufferError(f"Underflow exception, did not expect any more bytes here. {str(buffer)}")
            return UFrame(buffer, dest, source, repeaters, control_byte, poll_final, u_type)
    else:
        # S frame
        recv_seq = (control_byte & 0xE0) >> 5
        s_type = SupervisoryType.from_control_byte(control_byte)
        if next(byte_iter, None):
            raise BufferError(f"Underflow exception, did not expect any more bytes here. {str(buffer)}")
        return SFrame(buffer, dest, source, repeaters, control_byte, poll_final, recv_seq, s_type)


class AX25:
    def dl_error(self, remote_call: AX25Call, local_call: AX25Call, error_code):
        raise NotImplemented

    def dl_data(self, remote_call: AX25Call, local_call: AX25Call, protocol: L3Protocol, data: bytes):
        raise NotImplemented

    def dl_connect(self, remote_call: AX25Call, local_call: AX25Call):
        raise NotImplemented

    def dl_disconnect(self, remote_call: AX25Call, local_call: AX25Call):
        raise NotImplemented

    def write_packet(self, packet: AX25Packet):
        raise NotImplemented
