"""
AX.25 State Machine Code
"""
import asyncio
from asyncio.queues import Queue
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, cast, Dict

from asyncio import Future

from tarpn.ax25 import AX25Call, AX25Packet, IFrame, SFrame, SupervisoryType, UFrame, UnnumberedType, UIFrame, \
    L3Protocol, InternalInfo, SupervisoryCommand, AX25, DummyPacket
from tarpn.util import Timer


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

    @classmethod
    def dl_unit_data(cls, dest: AX25Call, protocol: L3Protocol, info: bytes):
        return cls(dest, InternalInfo.internal_info(protocol, info), AX25EventType.DL_UNIT_DATA)

    @classmethod
    def dl_data(cls, dest: AX25Call, protocol: L3Protocol, info: bytes):
        return cls(dest, InternalInfo.internal_info(protocol, info), AX25EventType.DL_DATA)

    @classmethod
    def dl_connect(cls, dest: AX25Call, source: AX25Call):
        dummy = DummyPacket.dummy(dest, source)
        return cls(dest, dummy, AX25EventType.DL_CONNECT)

    @classmethod
    def dl_disconnect(cls, dest: AX25Call, source: AX25Call):
        dummy = DummyPacket.dummy(dest, source)
        return cls(dest, dummy, AX25EventType.DL_DISCONNECT)


@dataclass
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
    ack_pending: bool = False
    pending_frames: Queue = Queue()
    srt: int = 1000
    ack_timer: Future = None
    reject_exception: bool = False
    layer_3: bool = False
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

    def clear_exception_conditions(self):
        #  // Clear peer busy
        #     // Clear reject exception
        #     // Clear own busy
        self.ack_pending = False

    def clear_pending_iframes(self):
        for i in range(self.pending_frames.qsize()):
            self.pending_frames.get()
            self.pending_frames.task_done()

    def push_iframe(self, i_frame):
        self.pending_frames.put(i_frame)

    def get_send_state(self):
        return self.vs % 8

    def set_send_state(self, vs: int):
        self.vs = vs

    def inc_send_state(self):
        self.vs += 1

    def get_recv_state(self):
        return self.vr % 8

    def set_recv_state(self, vr):
        self.vr = vr

    def inc_recv_state(self):
        self.vr += 1

    def get_ack_state(self):
        return self.va & 0xFF

    def set_ack_state(self, va):
        self.va = va & 0xFF

    def window_exceeded(self):
        """If V(S) is equal to V(A) + window size (7) means we can't transmit any more until we get an ACK"""
        return (self.vs % 8) == ((self.va + 7) % 8)

    def check_send_eq_ack(self):
        return self.vs % 8 == self.va

    def enqueue_info_ack(self, ax25: AX25):
        if self.ack_timer is None:
            self.ack_timer = asyncio.ensure_future(self.send_info_ack(ax25))
            self.ack_pending = True

    async def send_info_ack(self, ax25: AX25):
        await asyncio.sleep(0.040)
        if self.ack_pending:
            rr = SFrame.s_frame(self.remote_call, self.local_call, [], SupervisoryCommand.Response,
                                SupervisoryType.RR,
                                self.get_recv_state(), True)
            ax25.write_packet(rr)
            self.ack_pending = False


def check_ui(ui_frame: UIFrame, ax25: AX25):
    if ui_frame.get_command() == SupervisoryCommand.Command:
        # TODO check length, error K
        ax25.dl_data(ui_frame.source, ui_frame.dest, ui_frame.protocol, ui_frame.info)
    else:
        ax25.dl_error(ui_frame.source, ui_frame.dest, "Q")


def establish_data_link(state: AX25State, ax25: AX25):
    state.clear_exception_conditions()
    state.rc = 0
    sabm = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                          UnnumberedType.SABM, True)
    ax25.write_packet(sabm)
    state.t3.cancel()
    state.t1.start()


def transmit_enquiry(state: AX25State, ax25: AX25):
    rr = SFrame.s_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                        SupervisoryType.RR, state.get_recv_state(), True)
    ax25.write_packet(rr)
    state.ack_pending = False
    state.t1.start()


def enquiry_response(state: AX25State, ax25: AX25):
    rr = SFrame.s_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response, SupervisoryType.RR,
                        state.get_recv_state(), True)
    ax25.write_packet(rr)
    state.ack_pending = False


def select_t1_value(state: AX25State):
    if state.rc == 0:
        srt = 7./8. * state.srt + (1./8. * state.t1.delay) - (1./8. * state.t1.remaining())
        state.srt = srt
        state.t1.delay = srt * 2
    else:
        t1 = pow(2, (state.rc + 1.0) * state.srt)
        state.t1.delay = t1


def check_iframe_ack(state: AX25State, nr: int):
    if nr == state.get_send_state():
        state.set_ack_state(nr & 0xFF)
        state.t1.cancel()
        state.t3.start()
        select_t1_value(state)
    elif nr != state.get_ack_state():
        state.set_ack_state(nr & 0xFF)
        state.t1.start()


async def delay_outgoing_data(state: AX25State, pending: InternalInfo):
    await asyncio.sleep(0.200)
    state.push_iframe(pending)


def check_need_for_response(state: AX25State, ax25: AX25, s_frame: SFrame):
    if s_frame.poll_final:
        if s_frame.get_command() == SupervisoryCommand.Command:
            enquiry_response(state, ax25)
        elif s_frame.get_command() == SupervisoryCommand.Response:
            ax25.dl_error(state.remote_call, state.local_call, "A")


def nr_error_recovery(state: AX25State, ax25: AX25):
    ax25.dl_error(state.remote_call, state.local_call, "J")
    establish_data_link(state, ax25)
    state.layer_3 = False


def disconnected_handler(
        state: AX25State,
        event: AX25StateEvent,
        ax25: AX25) -> AX25StateType:
    """Handle packets when we are in a disconnected state
    """
    assert state.current_state == AX25StateType.Disconnected
    print(f"in disconnected state, got: {event}")
    if event.event_type == AX25EventType.AX25_UA:
        ax25.dl_error(event.packet.source, event.packet.dest, "C")
        ax25.dl_error(event.packet.source, event.packet.dest, "D")
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_DM:
        # do nothing
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_UI:
        ui_frame = cast(UIFrame, event.packet)
        check_ui(ui_frame, ax25)
        if ui_frame.poll_final:
            dm_response = UFrame.u_frame(ui_frame.source, ui_frame.dest, [], SupervisoryCommand.Response,
                                         UnnumberedType.DM, ui_frame.poll_final)
            ax25.write_packet(dm_response)
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.DL_DISCONNECT:
        ax25.dl_disconnect(event.packet.source, event.packet.dest)
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_DISC:
        u_frame = cast(UFrame, event.packet)
        dm_response = UFrame.u_frame(u_frame.source, u_frame.dest, [], SupervisoryCommand.Response,
                                     UnnumberedType.DM, u_frame.poll_final)
        ax25.write_packet(dm_response)
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.DL_UNIT_DATA:
        internal_info = cast(InternalInfo, event.packet)
        UIFrame.ui_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command, True,
                         internal_info.protocol, internal_info.info)
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.DL_DATA:
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.DL_CONNECT:
        establish_data_link(state, ax25)
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_SABM:
        sabm_frame = cast(UIFrame, event.packet)
        ua_resp = UFrame.u_frame(sabm_frame.source, sabm_frame.dest, [],
                                 SupervisoryCommand.Response, UnnumberedType.UA, True)
        ax25.write_packet(ua_resp)
        state.reset()
        ax25.dl_connect(sabm_frame.source, sabm_frame.dest)
        # TODO Set TIV (T initial value)
        state.t3.start()
        return AX25StateType.Connected
    elif event.event_type in (AX25EventType.AX25_RR, AX25EventType.AX25_RNR, AX25EventType.AX25_REJ,
                              AX25EventType.AX25_FRMR, AX25EventType.AX25_SREJ):
        s_frame = cast(SFrame, event.packet)
        # Send DM
        dm_response = UFrame.u_frame(s_frame.source, s_frame.dest, [], SupervisoryCommand.Response,
                                     UnnumberedType.DM, s_frame.poll_final)
        ax25.write_packet(dm_response)
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_INFO:
        i_frame = cast(IFrame, event.packet)
        dm_response = UFrame.u_frame(i_frame.source, i_frame.dest, [], SupervisoryCommand.Response,
                                     UnnumberedType.DM, i_frame.poll)
        ax25.write_packet(dm_response)
        return AX25StateType.Disconnected
    else:
        print(f"Ignoring {event}")
        return AX25StateType.Disconnected


def awaiting_connection_handler(
        state: AX25State,
        event: AX25StateEvent,
        ax25: AX25) -> AX25StateType:
    """Handle packets when we are in a awaiting connection state
    """
    assert state.current_state == AX25StateType.AwaitingConnection
    print(f"in awaiting connection state, got: {event}")
    if event.event_type == AX25EventType.DL_CONNECT:
        # TODO discard queue
        state.layer_3 = True
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_SABM:
        u_frame = cast(UFrame, event.packet)
        ua = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response,
                            UnnumberedType.UA, u_frame.poll_final)
        ax25.write_packet(ua)
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_DISC:
        u_frame = cast(UFrame, event.packet)
        dm = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response,
                            UnnumberedType.DM, u_frame.poll_final)
        ax25.write_packet(dm)
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.DL_DATA:
        # TODO if layer 3
        pending = cast(InternalInfo, event.packet)
        state.push_iframe(pending)
        return AX25StateType.AwaitingConnection
    elif not state.pending_frames.empty():
        pending = cast(InternalInfo, state.pending_frames.get())
        asyncio.ensure_future(delay_outgoing_data(state, pending))
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_UI:
        ui_frame = cast(UIFrame, event.packet)
        check_ui(ui_frame, ax25)
        if ui_frame.poll_final:
            dm = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response,
                                UnnumberedType.DM, True)
            ax25.write_packet(dm)
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.DL_UNIT_DATA:
        pending = cast(InternalInfo, event.packet)
        ui = UIFrame.ui_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                              True, pending.protocol, pending.info)
        ax25.write_packet(ui)
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_DM:
        u_frame = cast(UFrame, event.packet)
        if u_frame.poll_final:
            # TODO if layer 3
            ax25.dl_connect(state.remote_call, state.local_call)
            state.reset()
            select_t1_value(state)
            return AX25StateType.Connected
        else:
            ax25.dl_error(state.remote_call, state.local_call, "D")
            return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_SABME:
        dm = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response,
                            UnnumberedType.DM, True)
        ax25.write_packet(dm)
        ax25.dl_disconnect(state.remote_call, state.local_call)
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.T1_EXPIRE:
        if state.rc < 4: # TODO config this
            state.rc += 1
            sabm = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                                  UnnumberedType.SABM, True)
            ax25.write_packet(sabm)
            select_t1_value(state)
            state.t1.start()
            return AX25StateType.AwaitingConnection
        else:
            ax25.dl_error(state.remote_call, state.local_call, "G")
            ax25.dl_disconnect(state.remote_call, state.local_call)
            return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_FRMR:
        state.srt = 1000
        state.t1.delay = state.srt * 2
        establish_data_link(state, ax25)
        state.layer_3 = True
        return AX25StateType.AwaitingConnection
    else:
        print(f"Ignoring {event}")
        return AX25StateType.AwaitingConnection


def connected_handler(
        state: AX25State,
        event: AX25StateEvent,
        ax25: AX25) -> AX25StateType:
    assert state.current_state == AX25StateType.Connected
    print(f"in connected state, got: {event}")
    if event.event_type == AX25EventType.DL_CONNECT:
        state.clear_pending_iframes()
        establish_data_link(state, ax25)
        # Set Layer 3
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.DL_DISCONNECT:
        state.clear_pending_iframes()
        state.rc = 0
        u_frame = UFrame.u_frame(state.remote_call, state.local_call, SupervisoryCommand.Command,
                                 UnnumberedType.DISC, True)
        ax25.write_packet(u_frame)
        state.t3.cancel()
        state.t1.start()
        return AX25StateType.AwaitingRelease
    elif event.event_type == AX25EventType.DL_DATA:
        internal_info = cast(InternalInfo, event.packet)
        state.push_iframe(internal_info)
        return AX25StateType.Connected
    elif not state.pending_frames.empty():
        pending = cast(InternalInfo, state.pending_frames.get())
        if state.window_exceeded():
            asyncio.ensure_future(delay_outgoing_data(state, pending))
            state.pending_frames.task_done()
        i_frame = IFrame.i_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command, False,
                                 state.get_send_state(), state.get_recv_state(), pending.protocol, pending.info)
        ax25.write_packet(i_frame)
        # TODO store sent iframe?
        state.vs += 1
        state.ack_pending = False
        if state.t1.running():
            state.t3.cancel()
            state.t1.start()
        state.pending_frames.task_done()
        return AX25StateType.Connected
    elif event.event_type == AX25EventType.T1_EXPIRE:
        state.rc = 1
        transmit_enquiry(state, ax25)
        return AX25StateType.TimerRecovery
    elif event.event_type == AX25EventType.T3_EXPIRE:
        state.rc = 0
        transmit_enquiry(state, ax25)
        return AX25StateType.TimerRecovery
    elif event.event_type == AX25EventType.AX25_SABM:
        u_frame = cast(UFrame, event.packet)
        ua = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response,
                            UnnumberedType.UA, u_frame.poll_final)
        ax25.write_packet(ua)
        state.clear_exception_conditions()
        ax25.dl_error(state.remote_call, state.local_call, "F")
        if state.get_send_state() == state.get_ack_state():
            state.clear_pending_iframes()
            ax25.dl_connect(state.remote_call, state.local_call)
        state.reset()
        return AX25StateType.Connected
    elif event.event_type == AX25EventType.AX25_DISC:
        state.clear_pending_iframes()
        u_frame = cast(UFrame, event.packet)
        ua = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response,
                            UnnumberedType.UA, u_frame.poll_final)
        ax25.write_packet(ua)
        ax25.dl_disconnect(state.remote_call, state.local_call)
        state.t1.cancel()
        state.t3.cancel()
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_UA:
        ax25.dl_error(state.remote_call, state.local_call, "C")
        establish_data_link(ax25)
        state.layer_3 = False
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_DM:
        ax25.dl_error(state.remote_call, state.local_call, "E")
        ax25.dl_disconnect(state.remote_call, state.local_call)
        state.clear_pending_iframes()
        state.t1.cancel()
        state.t3.cancel()
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.DL_UNIT_DATA:
        info = cast(InternalInfo, state.pending_frames.get())
        ui_frame = UIFrame.ui_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command, True,
                                    info.protocol, info.info)
        ax25.write_packet(ui_frame)
        return AX25StateType.Connected
    elif event.event_type == AX25EventType.AX25_UI:
        ui_frame = cast(UIFrame, event.packet)
        ax25.dl_data(state.remote_call, state.local_call, ui_frame.protocol, ui_frame.info)
        if ui_frame.poll_final:
            enquiry_response(state, ax25)
        return AX25StateType.Connected
    elif event.event_type in (AX25EventType.AX25_RR, AX25EventType.AX25_RNR):
        # TODO set peer busy if RNR, else clear peer busy
        s_frame = cast(SFrame, event.packet)
        check_need_for_response(state, ax25, s_frame)
        if s_frame.receive_seq_number <= state.get_send_state():
            check_iframe_ack(state, s_frame.receive_seq_number)
            return AX25StateType.Connected
        else:
            print("N(R) error recovery, re-establishing connection")
            nr_error_recovery(state, ax25)
            return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_INFO:
        i_frame = cast(IFrame, event.packet)
        if i_frame.get_command() == SupervisoryCommand.Command:
            if state.get_ack_state() <= i_frame.receive_seq_number <= state.get_send_state():
                check_iframe_ack(state, i_frame.receive_seq_number)
                if i_frame.send_seq_number == state.get_recv_state():
                    state.inc_recv_state()
                    state.reject_exception = False
                    ax25.dl_data(state.remote_call, state.local_call, i_frame.protocol, i_frame.info)
                    if i_frame.poll:
                        # Set N(R) = V(R)
                        state.enqueue_info_ack(ax25)
                else:
                    if state.reject_exception:
                        if i_frame.poll:
                            rr = SFrame.s_frame(state.remote_call, state.local_call, SupervisoryCommand.Response,
                                                SupervisoryType.RR, state.get_recv_state(), True)
                            ax25.write_packet(rr)
                            state.ack_pending = False
                    else:
                        state.reject_exception = True
                        rej = SFrame.s_frame(state.remote_call, state.local_call, SupervisoryCommand.Response,
                                             SupervisoryType.REJ, state.get_recv_state(), i_frame.poll)
                        ax25.write_packet(rej)
                return AX25StateType.Connected
            else:
                nr_error_recovery(state, ax25)
                return AX25StateType.AwaitingConnection
        else:
            ax25.dl_error(state.remote_call, state.local_call, "S")
            return AX25StateType.Connected
    elif event.event_type == AX25EventType.AX25_FRMR:
        ax25.dl_error(state.remote_call, state.local_call, "K")
        establish_data_link(state, ax25)
        state.layer_3 = True
        return AX25StateType.Connected
    else:
        print(f"Ignoring {event}")
        return AX25StateType.Connected


class AX25StateMachine:
    """State management for AX.25 Data Links

    Holds a mapping of AX.25 sessions keyed on remote callsign.

    """
    def __init__(self, ax25: AX25):
        self._ax25 = ax25
        self._sessions: Dict[str, AX25State] = {}
        self._handlers = {
            AX25StateType.Disconnected: disconnected_handler,
            AX25StateType.Connected: connected_handler,
            AX25StateType.AwaitingConnection: awaiting_connection_handler
        }


    def handle_packet(self, packet: AX25Packet):
        state = self._sessions.get(str(packet.source))
        if state is None:
            state = AX25State.create(packet.source, packet.dest, self.handle_internal_event)
            self._sessions[str(packet.source)] = state
        event = AX25StateEvent.from_packet(packet)
        handler = self._handlers[state.current_state]
        if handler is None:
            raise RuntimeError(f"No handler for {handler}")
        new_state = self._handlers[state.current_state](state, event, self._ax25)
        state.current_state = new_state

    def handle_internal_event(self, event: AX25StateEvent):
        state = self._sessions.get(str(event.remote_call))
        if not state:
            raise RuntimeError(f"No session for timer event {event}")
        handler = self._handlers[state.current_state]
        if handler is None:
            raise RuntimeError(f"No handler for {handler}")
        new_state = self._handlers[state.current_state](state, event, self._ax25)
        state.current_state = new_state
