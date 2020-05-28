from dataclasses import dataclass, field
from enum import IntEnum, Enum, auto
from typing import Iterator, List, Dict, Callable, cast

from tarpn.util import Timer


@dataclass
class AX25Call:
    callsign: str
    ssid: int
    rr: int = field(default=0, compare=False)
    c_flag: bool = field(default=False, compare=False)
    last: bool = field(default=False, compare=False)

    def __repr__(self):
        return f"{self.callsign}-{self.ssid}"

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


class SupervisoryCommand(Enum):
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
    def build(cls,
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

    @classmethod
    def build(cls,
              dest: AX25Call,
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


class Protocol(IntEnum):
    NoProtocol = 0x00
    NetRom = 0xCF
    NoLayer3 = 0xF0
    # TODO include others?


@dataclass
class UIFrame(UFrame):
    info: bytes
    protocol: Protocol


@dataclass
class IFrame(AX25Packet):
    poll: bool
    receive_seq_number: int
    send_seq_number: int
    info: bytes
    protocol: Protocol


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
        protocol = Protocol(pid_byte)
        info = bytes(byte_iter)
        recv_seq = (control_byte >> 5) & 0x07
        send_seq = (control_byte >> 1) & 0x07
        if next(byte_iter, None):
            raise BufferError(f"Underflow exception, did not expect any more bytes here. {buffer}")
        return IFrame(buffer, dest, source, repeaters, control_byte, poll_final, recv_seq, send_seq, info, protocol)
    elif (control_byte & 0x03) == 0x03:
        # U frame
        u_type = UnnumberedType.from_control_byte(control_byte)
        if u_type == UnnumberedType.UI:
            pid_byte = next(byte_iter)
            protocol = Protocol(pid_byte)
            info = bytes(byte_iter)
            if next(byte_iter, None):
                raise BufferError(f"Underflow exception, did not expect any more bytes here. {buffer}")
            return UIFrame(buffer, dest, source, repeaters, control_byte, poll_final, u_type, info, protocol)
        else:
            if next(byte_iter, None):
                raise BufferError(f"Underflow exception, did not expect any more bytes here. {buffer}")
            return UFrame(buffer, dest, source, repeaters, control_byte, poll_final, u_type)
    else:
        # S frame
        recv_seq = (control_byte & 0xE0) >> 5
        s_type = SupervisoryType.from_control_byte(control_byte)
        if next(byte_iter, None):
            raise BufferError(f"Underflow exception, did not expect any more bytes here. {buffer}")
        return SFrame(buffer, dest, source, repeaters, control_byte, poll_final, recv_seq, s_type)


"""
State Machine stuff
"""


class AX25:
    def dl_error(self, error_code):
        raise NotImplemented

    def dl_data(self, data):
        raise NotImplemented

    def dl_connect(self, remote_call: AX25Call):
        raise NotImplemented

    def dl_disconnect(self, remote_call: AX25Call):
        raise NotImplemented

    def write_packet(self, packet: AX25Packet):
        raise NotImplemented


class AX25EventType(Enum):
    AX25_UA = auto()
    AX25_DM = auto()
    AX25_UI = auto()
    AX25_DISC = auto()
    AX25_SABM = auto()
    AX25_SABME = auto()
    AX25_UNKNOWN = auto()
    AX25_INFO = auto()
    AX25_FRMR = auto()
    AX25_RR = auto()
    AX25_RNR = auto()
    AX25_SREJ = auto()
    AX25_REJ = auto()
    T1_EXPIRE = auto()
    T3_EXPIRE = auto()
    DL_CONNECT = auto()
    DL_DISCONNECT = auto()
    DL_DATA = auto()
    DL_UNIT_DATA = auto()
    # DL_FLOW_OFF,
    # DL_FLOW_ON,
    IFRAME_READY = auto()


class AX25StateType(Enum):
    Disconnected = auto()
    AwaitingConnection = auto()
    Connected = auto()
    AwaitingRelease = auto()
    TimerRecovery = auto()


@dataclass
class AX25StateEvent:
    remote_call: AX25Call
    packet: AX25Packet
    event_type: AX25EventType

    @classmethod
    def t1_expire(cls, retry_call: AX25Call):
        return cls(retry_call, None, AX25EventType.T1_EXPIRE)

    @classmethod
    def t3_expire(cls, retry_call: AX25Call):
        return cls(retry_call, None, AX25EventType.T3_EXPIRE)

    @classmethod
    def from_packet(cls, packet: AX25Packet):
        if isinstance(packet, IFrame):
            return cls(packet.source, packet, AX25EventType.AX25_INFO)
        elif isinstance(packet, SFrame):
            event_type = {
                SupervisoryType.RR: AX25EventType.AX25_RR,
                SupervisoryType.RNR: AX25EventType.AX25_RNR,
                SupervisoryType.REJ: AX25EventType.AX25_REJ
            }.get(packet.control_type, AX25EventType.AX25_UNKNOWN)
            return cls(packet.source, packet, event_type)
        elif isinstance(packet, UFrame):
            event_type = {
                UnnumberedType.DISC: AX25EventType.AX25_DISC,
                UnnumberedType.DM: AX25EventType.AX25_DM,
                UnnumberedType.FRMR: AX25EventType.AX25_FRMR,
                UnnumberedType.SABM: AX25EventType.AX25_SABM,
                UnnumberedType.UA: AX25EventType.AX25_UA,
                UnnumberedType.UI: AX25EventType.AX25_UI,
            }.get(packet.u_type, AX25EventType.AX25_UNKNOWN)
            return cls(packet.source, packet, event_type)
        elif isinstance(packet, UIFrame):
            return cls(packet.source, packet, AX25EventType.AX25_UI)
        else:
            return cls(packet.source, packet, AX25EventType.AX25_UNKNOWN)


@dataclass()
class AX25State:
    """Represents the internal state of an AX.25 connection. This is used in conjunction with
    the state machine to manage the connection state and interface with the DL (Data-Link) layer
    """
    session_id: str
    """Unique key for this state, by default the remote callsign+ssid"""

    remote_call: AX25Call
    """Remote station connecting to the local node"""

    local_call: AX25Call
    """Local station's callsign"""

    internal_event_cb: Callable[[AX25StateEvent], None] = field(repr=False)
    """Callback for internal state machine events such as timeouts"""

    t1: Timer = field(default=None, repr=False)
    """T1 timer. This is a timeout for hearing Info acks in connected mode"""

    t3: Timer = field(default=None, repr=False)
    """T3 timer. This is an idle timeout. Ensure's that a link is still alive"""

    current_state: AX25StateType = AX25StateType.Disconnected
    vs: int = 0
    vr: int = 0
    va: int = 0
    rc: int = 0
    # TODO other fields

    @classmethod
    def create(cls,
               remote_call: AX25Call,
               local_call: AX25Call,
               internal_event_cb: Callable[[AX25StateEvent], None]):
        new_state = cls(str(remote_call), remote_call, local_call, internal_event_cb)
        new_state.t1 = Timer(4, new_state.t1_timeout)
        new_state.t3 = Timer(1, new_state.t3_timeout)
        return new_state

    async def t1_timeout(self):
        print(f"t1 for {self}")
        self.internal_event_cb(AX25StateEvent.t1_expire(self.remote_call))

    async def t3_timeout(self):
        print(f"t3 for {self}")
        self.internal_event_cb(AX25StateEvent.t3_expire(self.remote_call))

    def reset(self):
        self.vs = 0
        self.vr = 0
        self.va = 0
        self.rc = 0
        self.t1.cancel()
        self.t3.cancel()


def check_ui(ui_frame: UIFrame, ax25: AX25):
    if ui_frame.get_command() == SupervisoryCommand.Command:
        # TODO check length, error K
        ax25.dl_data(ui_frame.info)
    else:
        ax25.dl_error("Q")


def disconnected_handler(
        state: AX25State,
        event: AX25StateEvent,
        ax25: AX25) -> AX25StateType:
    """Handle packets when we are in a disconnected state
    """
    assert state.current_state == AX25StateType.Disconnected
    print(f"in disconnected state, got: {event}")
    if event.event_type == AX25EventType.AX25_UA:
        # send DL error C and D
        ax25.dl_error("C")
        ax25.dl_error("D")
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_DM:
        # do nothing
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_UI:
        ui_frame = cast(UIFrame, event.packet)

        # UI check
        check_ui(ui_frame, ax25)

        # If p/f, send DM
        if ui_frame.poll_final:
            dm_response = UFrame.build(ui_frame.source, ui_frame.dest, [], SupervisoryCommand.Response,
                                       UnnumberedType.DM, ui_frame.poll_final)
            ax25.write_packet(dm_response)
        return AX25StateType.Disconnected
    elif event.event_type in (AX25EventType.AX25_RR, AX25EventType.AX25_RNR, AX25EventType.AX25_REJ):
        s_frame = cast(SFrame, event.packet)
        dm_response = UFrame.build(s_frame.source, s_frame.dest, [], SupervisoryCommand.Response,
                                   UnnumberedType.DM, s_frame.poll_final)
        ax25.write_packet(dm_response)
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_SABM:
        sabm_frame = cast(UIFrame, event.packet)

        # Send UA
        ua_resp = UFrame.build(sabm_frame.source, sabm_frame.dest, [],
                               SupervisoryCommand.Response, UnnumberedType.UA, True)
        ax25.write_packet(ua_resp)

        # Reset exceptions, state values, and timers
        state.reset()

        # Notify DL layer
        ax25.dl_connect(sabm_frame.source)

        # Set TIV (T initial value)
        # Start T3 timer
        state.t3.start()

        return AX25StateType.Connected


class AX25StateMachine:
    def __init__(self, ax25: AX25):
        self._ax25 = ax25
        self._sessions: Dict[str, AX25State] = {}
        self._handlers = {
            AX25StateType.Disconnected: disconnected_handler
        }

    def new_state(self, packet: AX25Packet):
        AX25State.create(packet, self._ax25)

    def handle_packet(self, packet: AX25Packet):
        state = self._sessions.get(str(packet.dest), AX25State.create(
            packet.source, packet.dest, self.handle_internal_event))
        event = AX25StateEvent.from_packet(packet)
        self._handlers[state.current_state](state, event, self._ax25)

    def handle_internal_event(self, event: AX25StateEvent):
        state = self._sessions.get(str(event.remote_call))
        if not state:
            raise RuntimeError("Got a timer callback for a non-existent session")
        self._handlers[state.current_state](state, event, self._ax25)

    def handle_timer(self, session_id: str):
        state = self._sessions.get(session_id)
        if not state:
            raise RuntimeError("Got a timer callback for a non-existent session")
        # TODO create timer AX25StateEvent
        event = None
        self._handlers[state.current_state](state, event, self._ax25)