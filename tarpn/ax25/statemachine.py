"""
AX.25 State Machine Code
"""
import asyncio
from asyncio import Future
import queue
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import partial
import logging
from typing import Callable, cast, Dict, Optional, List, Tuple

from tarpn.ax25 import AX25Call, AX25Packet, IFrame, SFrame, SupervisoryType, UFrame, UnnumberedType, UIFrame, \
    L3Protocol, InternalInfo, SupervisoryCommand, DummyPacket, AX25, AX25StateType
from tarpn.log import LoggingMixin
from tarpn.util import AsyncioTimer, between, Timer


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

    def __repr__(self):
        return self.name

    def __str__(self):
        return self.name


@dataclass
class AX25StateEvent:
    remote_call: AX25Call
    packet: Optional[AX25Packet]
    event_type: AX25EventType
    future: Future = None

    def __repr__(self):
        if self.packet is not None:
            return f"{self.event_type} {self.packet}"
        else:
            return f"{self.event_type}"

    @classmethod
    def t1_expire(cls, remote_call: AX25Call):
        return cls(remote_call, None, AX25EventType.T1_EXPIRE)

    @classmethod
    def t3_expire(cls, remote_call: AX25Call):
        return cls(remote_call, None, AX25EventType.T3_EXPIRE)

    @classmethod
    def iframe_ready(cls, remote_call: AX25Call):
        return cls(remote_call, None, AX25EventType.IFRAME_READY)

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
    """
    V(S) Send State Variable
    The send state variable exists within the TNC and is never sent. It contains the next sequential number to be 
    assigned to the next transmitted I frame. This variable is updated with the transmission of each I frame.
    
    N(S) Send Sequence Number
    The send sequence number is found in the control field of all I frames. It contains the sequence number of the 
    I frame being sent. Just prior to the transmission of the I frame, N(S) is updated to equal the send state variable.
    """

    vr: int = 0
    """
    V(R) Receive State Variable
    The receive state variable exists within the TNC. It contains the sequence number of the next expected received 
    I frame. This variable is updated upon the reception of an error-free I frame whose send sequence number equals the 
    present received state variable value.
    
    N(R) Received Sequence Number
    The received sequence number exists in both I and S frames. Prior to sending an I or S frame, this variable is 
    updated to equal that of the received state variable, thus implicitly acknowledging the proper reception of all 
    frames up to and including N(R)-1
    """

    va: int = 0
    """
    V(A) Acknowledge State Variable
    The acknowledge state variable exists within the TNC and is never sent. It contains the sequence number of the last 
    frame acknowledged by its peer [V(A)-1 equals the N(S) of the last acknowledged I frame].
    """

    retry_count: int = 0
    """Seen as RC in the specification"""

    ack_pending: bool = False

    smoothed_roundtrip_time_ms: int = 1000
    """Seen as SRT in the specification"""

    reject_exception: bool = False

    layer_3: bool = False
    # TODO other fields

    pending_frames: queue.Queue = field(default_factory=queue.Queue, repr=False)  # (InternalInfo, Future)

    sent_frames: Dict[int, IFrame] = field(default_factory=dict, repr=False)

    futures: Dict[int, Future] = field(default_factory=dict, repr=False)

    def log_prefix(self):
        return f"AX25 [Id={self.session_id} Local={self.local_call} Remote={self.remote_call} State={self.current_state}]"

    @classmethod
    def create(cls,
               remote_call: AX25Call,
               local_call: AX25Call,
               internal_event_cb: Callable[[AX25StateEvent], None],
               timer_factory: Callable[[float, Callable[[], None]], Timer]):

        def async_callback(event: AX25StateEvent) -> None:
            cb = timer_factory(0, partial(internal_event_cb, event))
            cb.start()

        new_state = cls(str(remote_call), remote_call, local_call, async_callback)
        new_state.t1 = timer_factory(1_000, new_state.t1_timeout)  # TODO configure these
        new_state.t3 = timer_factory(180_000, new_state.t3_timeout)
        return new_state

    def t1_timeout(self):
        self.internal_event_cb(AX25StateEvent.t1_expire(self.remote_call))

    def t3_timeout(self):
        self.internal_event_cb(AX25StateEvent.t3_expire(self.remote_call))

    def reset(self):
        self.vs = 0
        self.vr = 0
        self.va = 0
        self.retry_count = 0
        self.smoothed_roundtrip_time_ms = 1_000
        self.t1.delay = 1_000
        self.t1.cancel()
        self.t3.cancel()

    def clear_exception_conditions(self):
        #  // Clear peer busy
        #     // Clear reject exception
        #     // Clear own busy
        self.ack_pending = False

    def clear_pending_iframes(self):
        for i in range(self.pending_frames.qsize()):
            (iframe, future) = self.pending_frames.get()
            future.cancel("Cleared")
            self.pending_frames.task_done()

    def push_iframe(self, i_frame: InternalInfo, future: Future):
        self.pending_frames.put((i_frame, future))
        self.internal_event_cb(AX25StateEvent.iframe_ready(self.remote_call))

    def get_send_state(self):
        return self.vs % 8

    def set_send_state(self, vs: int):
        #self.print_window()
        self.vs = vs

    def inc_send_state(self):
        #self.print_window()
        self.vs += 1

    def get_recv_state(self):
        return self.vr % 8

    def set_recv_state(self, vr):
        #self.print_window()
        self.vr = vr

    def inc_recv_state(self):
        #self.print_window()
        self.vr += 1

    def get_ack_state(self):
        return self.va & 0xFF

    def set_ack_state(self, va):
        #self.print_window()
        self.va = va & 0xFF

    def print_window(self):
        print(f"{self.local_call} V(A)={self.get_ack_state()} V(R)={self.get_recv_state()} V(S)={self.get_send_state()}"
              f" window_exceeded={self.window_exceeded()}")

    def window_exceeded(self):
        """If V(S) is equal to V(A) + window size (7) means we can't transmit any more until we get an ACK"""
        return (self.vs % 8) == ((self.va + 7) % 8)

    def check_send_eq_ack(self):
        return self.vs % 8 == self.va

    def enqueue_info_ack(self, ax25: AX25, final=True):
        rr = SFrame.s_frame(self.remote_call, self.local_call, [], SupervisoryCommand.Response,
                            SupervisoryType.RR,
                            self.get_recv_state(), final)
        ax25.write_packet(rr)
        self.ack_pending = False


def check_ui(ui_frame: UIFrame, ax25: AX25):
    if ui_frame.get_command() == SupervisoryCommand.Command:
        # TODO check length, error K
        ax25.dl_data_indication(ui_frame.source, ui_frame.dest, ui_frame.protocol, ui_frame.info)
    else:
        ax25.dl_error(ui_frame.source, ui_frame.dest, "Q")


def establish_data_link(state: AX25State, ax25: AX25):
    state.clear_exception_conditions()
    state.retry_count = 0
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


def enquiry_response(state: AX25State, ax25: AX25, final=True):
    rr = SFrame.s_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response, SupervisoryType.RR,
                        state.get_recv_state(), final)
    ax25.write_packet(rr)
    state.ack_pending = False


def select_t1_value(state: AX25State):
    if state.retry_count == 0:
        srt = 7. / 8. * state.smoothed_roundtrip_time_ms + (1. / 8. * state.t1.delay) - (1. / 8. * state.t1.remaining())
        state.smoothed_roundtrip_time_ms = srt
        state.t1.delay = srt * 2
    else:
        t1 = pow(2, (state.retry_count + 1.0)) * state.smoothed_roundtrip_time_ms
        state.t1.delay = t1


def check_iframe_ack(state: AX25State, nr: int):
    if nr == state.get_send_state():
        state.set_ack_state(nr & 0xFF)
        vs = (nr + 7) % 8
        fut = state.futures.get(vs)
        if fut and not fut.done():
            fut.set_result(None)
        state.t1.cancel()
        state.t3.start()
        select_t1_value(state)
    elif nr != state.get_ack_state():
        state.set_ack_state(nr & 0xFF)
        vs = (nr + 7) % 8
        fut = state.futures.get(vs)
        if fut and not fut.done():
            fut.set_result(None)
        state.t1.start()


async def delay_outgoing_data(state: AX25State, pending: InternalInfo, future: Future):
    await asyncio.sleep(0.200)
    state.push_iframe(pending, future)


def check_need_for_response(state: AX25State, ax25: AX25, s_frame: SFrame):
    if s_frame.get_command() and s_frame.poll_final:
        enquiry_response(state, ax25)
    elif s_frame.get_command() == SupervisoryCommand.Response and s_frame.poll_final:
        ax25.dl_error(state.remote_call, state.local_call, "A")


def check_nr(state: AX25State, nr: int):
    if state.get_send_state() < state.get_ack_state():
        # Window wrap-around case
        return between(nr, state.get_ack_state(), 7) or \
                between(nr, 0, state.get_send_state())
    else:
        return between(nr, state.get_ack_state(), state.get_send_state())


def nr_error_recovery(state: AX25State, ax25: AX25):
    ax25.dl_error(state.remote_call, state.local_call, "J")
    establish_data_link(state, ax25)
    state.layer_3 = False


def invoke_retransmission(state: AX25State, ax25: AX25):
    x = state.get_send_state()
    vs = state.get_recv_state()
    while vs != x:
        old_frame = state.sent_frames[vs]
        old_future = state.futures[vs]
        state.push_iframe(InternalInfo.internal_info(old_frame.protocol, old_frame.info), old_future)
        vs += 1


def disconnected_handler(
        state: AX25State,
        event: AX25StateEvent,
        ax25: AX25,
        logger: LoggingMixin) -> AX25StateType:
    """
    Handle packets when we are in a disconnected state
    """
    assert state.current_state == AX25StateType.Disconnected
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
        ax25.dl_disconnect_indication(event.packet.source, event.packet.dest)
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_DISC:
        u_frame = cast(UFrame, event.packet)
        dm_response = UFrame.u_frame(u_frame.source, u_frame.dest, [], SupervisoryCommand.Response,
                                     UnnumberedType.DM, u_frame.poll_final)
        ax25.write_packet(dm_response)
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.DL_UNIT_DATA:
        internal_info = cast(InternalInfo, event.packet)
        ui = UIFrame.ui_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command, False,
                              internal_info.protocol, internal_info.info)
        ax25.write_packet(ui)
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.DL_DATA:
        event.future.set_exception(RuntimeError("Not connected"))
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.DL_CONNECT:
        state.reset()
        establish_data_link(state, ax25)
        state.layer_3 = True
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_SABM:
        sabm_frame = cast(UIFrame, event.packet)
        ua_resp = UFrame.u_frame(sabm_frame.source, sabm_frame.dest, [],
                                 SupervisoryCommand.Response, UnnumberedType.UA, True)
        ax25.write_packet(ua_resp)
        state.reset()
        ax25.dl_connect_indication(sabm_frame.source, sabm_frame.dest)
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
        logger.debug(f"Ignoring {event}")
        return AX25StateType.Disconnected


def awaiting_connection_handler(
        state: AX25State,
        event: AX25StateEvent,
        ax25: AX25,
        logger: LoggingMixin) -> AX25StateType:
    """
    Handle packets when we are in a awaiting connection state
    """
    assert state.current_state == AX25StateType.AwaitingConnection
    if event.event_type == AX25EventType.DL_CONNECT:
        state.clear_pending_iframes()
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
        if not state.layer_3:
            pending = cast(InternalInfo, event.packet)
            state.push_iframe(pending, event.future)
        else:
            event.future.set_exception(RuntimeError("Not connected"))
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.IFRAME_READY:
        if not state.layer_3:
            (pending, future) = state.pending_frames.get()
            #asyncio.ensure_future(delay_outgoing_data(state, pending, future))
            state.push_iframe(pending, future)
            state.pending_frames.task_done()
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
                              False, pending.protocol, pending.info)
        ax25.write_packet(ui)
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_DM:
        u_frame = cast(UFrame, event.packet)
        if u_frame.poll_final:
            state.clear_pending_iframes()
            ax25.dl_disconnect_indication(state.remote_call, state.local_call)
            state.t1.cancel()
            return AX25StateType.Disconnected
        else:
            return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_UA:
        u_frame = cast(UFrame, event.packet)
        if u_frame.poll_final:
            if state.layer_3:
                ax25.dl_connect_indication(state.remote_call, state.local_call)
            else:
                if state.get_send_state() != state.get_ack_state():
                    state.clear_pending_iframes()
                    ax25.dl_connect_indication(state.remote_call, state.local_call)
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
        ax25.dl_disconnect_indication(state.remote_call, state.local_call)
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.T1_EXPIRE:
        if state.retry_count < 4:  # TODO config this
            state.retry_count += 1
            sabm = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                                  UnnumberedType.SABM, True)
            ax25.write_packet(sabm)
            select_t1_value(state)
            state.t1.start()
            return AX25StateType.AwaitingConnection
        else:
            ax25.dl_error(state.remote_call, state.local_call, "G")
            ax25.dl_disconnect_indication(state.remote_call, state.local_call)
            return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_FRMR:
        state.smoothed_roundtrip_time_ms = 1000
        state.t1.delay = state.smoothed_roundtrip_time_ms * 2
        establish_data_link(state, ax25)
        state.layer_3 = True
        return AX25StateType.AwaitingConnection
    else:
        logger.debug(f"Ignoring {event}")
        return AX25StateType.AwaitingConnection


def connected_handler(
        state: AX25State,
        event: AX25StateEvent,
        ax25: AX25,
        logger: LoggingMixin) -> AX25StateType:
    assert state.current_state == AX25StateType.Connected
    if event.event_type == AX25EventType.DL_CONNECT:
        state.clear_pending_iframes()
        establish_data_link(state, ax25)
        # Set Layer 3
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.DL_DISCONNECT:
        state.clear_pending_iframes()
        state.retry_count = 0
        u_frame = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                                 UnnumberedType.DISC, True)
        ax25.write_packet(u_frame)
        state.t3.cancel()
        state.t1.start()
        return AX25StateType.AwaitingRelease
    elif event.event_type == AX25EventType.DL_DATA:
        pending = cast(InternalInfo, event.packet)
        state.push_iframe(pending, event.future)
        return AX25StateType.Connected
    elif event.event_type == AX25EventType.IFRAME_READY:
        (pending, future) = cast(InternalInfo, state.pending_frames.get())
        logger.debug(f"Pending iframe: {pending}")
        if state.window_exceeded():
            logger.debug(f"Window exceeded, delaying frame")
            #asyncio.create_task(delay_outgoing_data(state, pending, future))
            state.push_iframe(pending, future)
        else:
            i_frame = IFrame.i_frame(
                dest=state.remote_call,
                source=state.local_call,
                repeaters=[],
                command=SupervisoryCommand.Command,
                poll=False,  # Ensure we get RR from other end
                receive_seq_number=state.get_recv_state(),
                send_seq_number=state.get_send_state(),
                protocol=pending.protocol,
                info=pending.info)
            ax25.write_packet(i_frame)
            state.sent_frames[state.get_send_state()] = i_frame
            state.futures[state.get_send_state()] = future
            # Complete the future indicating the DL_DATA event was sent out
            if future:
                future.set_result(None)
            state.inc_send_state()
            state.ack_pending = False
            if state.t1.running():
                state.t3.cancel()
                state.t1.start()
        state.pending_frames.task_done()
        return AX25StateType.Connected
    elif event.event_type == AX25EventType.T1_EXPIRE:
        state.retry_count = 1
        transmit_enquiry(state, ax25)
        return AX25StateType.TimerRecovery
    elif event.event_type == AX25EventType.T3_EXPIRE:
        state.retry_count = 0
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
            ax25.dl_connect_indication(state.remote_call, state.local_call)
        state.reset()
        return AX25StateType.Connected
    elif event.event_type == AX25EventType.AX25_DISC:
        state.clear_pending_iframes()
        u_frame = cast(UFrame, event.packet)
        ua = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response,
                            UnnumberedType.UA, u_frame.poll_final)
        ax25.write_packet(ua)
        ax25.dl_disconnect_indication(state.remote_call, state.local_call)
        state.t1.cancel()
        state.t3.cancel()
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_UA:
        ax25.dl_error(state.remote_call, state.local_call, "C")
        establish_data_link(state, ax25)
        state.layer_3 = False
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_DM:
        ax25.dl_error(state.remote_call, state.local_call, "E")
        ax25.dl_disconnect_indication(state.remote_call, state.local_call)
        state.clear_pending_iframes()
        state.t1.cancel()
        state.t3.cancel()
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.DL_UNIT_DATA:
        pending = cast(InternalInfo, event.packet)
        ui = UIFrame.ui_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                              False, pending.protocol, pending.info)
        ax25.write_packet(ui)
        return AX25StateType.Connected
    elif event.event_type == AX25EventType.AX25_UI:
        ui_frame = cast(UIFrame, event.packet)
        ax25.dl_data_indication(state.remote_call, state.local_call, ui_frame.protocol, ui_frame.info)
        if ui_frame.poll_final:
            enquiry_response(state, ax25)
        return AX25StateType.Connected
    elif event.event_type in (AX25EventType.AX25_RR, AX25EventType.AX25_RNR):
        # TODO set peer busy if RNR, else clear peer busy
        s_frame = cast(SFrame, event.packet)
        check_need_for_response(state, ax25, s_frame)

        if check_nr(state, s_frame.receive_seq_number):
            check_iframe_ack(state, s_frame.receive_seq_number)
            return AX25StateType.Connected
        else:
            logger.warning(f"N(R) error recovery, V(A)={state.get_ack_state()} N(R)={s_frame.receive_seq_number} "
                           f"V(S)={state.get_send_state()}")
            nr_error_recovery(state, ax25)
            return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_INFO:
        i_frame = cast(IFrame, event.packet)
        if i_frame.get_command() == SupervisoryCommand.Command:
            if check_nr(state, i_frame.receive_seq_number):
                check_iframe_ack(state, i_frame.receive_seq_number)
                if i_frame.send_seq_number == state.get_recv_state():
                    state.inc_recv_state()
                    state.reject_exception = False
                    state.enqueue_info_ack(ax25, i_frame.poll)
                    # This should be before the info ack in theory
                    ax25.dl_data_indication(state.remote_call, state.local_call, i_frame.protocol, i_frame.info)
                else:
                    if state.reject_exception:
                        if i_frame.poll:
                            rr = SFrame.s_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response,
                                                SupervisoryType.RR, state.get_recv_state(), True)
                            ax25.write_packet(rr)
                            state.ack_pending = False
                    else:
                        state.reject_exception = True
                        rej = SFrame.s_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response,
                                             SupervisoryType.REJ, state.get_recv_state(), i_frame.poll)
                        ax25.write_packet(rej)
                return AX25StateType.Connected
            else:
                logger.warning(f"N(R) error recovery, V(A)={state.get_ack_state()} N(R)={i_frame.receive_seq_number} "
                               f"V(S)={state.get_send_state()}")
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
        logger.debug(f"Ignoring {event}")
        return AX25StateType.Connected


def timer_recovery_handler(
        state: AX25State,
        event: AX25StateEvent,
        ax25: AX25,
        logger: LoggingMixin) -> AX25StateType:
    assert state.current_state == AX25StateType.TimerRecovery
    if event.event_type == AX25EventType.DL_CONNECT:
        state.clear_pending_iframes()
        establish_data_link(state, ax25)
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.DL_DISCONNECT:
        state.clear_pending_iframes()
        state.retry_count = 0
        disc = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                              UnnumberedType.DISC, True)
        ax25.write_packet(disc)
        state.t3.cancel()
        state.t1.start()
        return AX25StateType.AwaitingRelease
    elif event.event_type == AX25EventType.DL_DATA:
        pending = cast(InternalInfo, event.packet)
        state.push_iframe(pending, event.future)
        return AX25StateType.TimerRecovery
    elif event.event_type == AX25EventType.IFRAME_READY:
        pending, future = state.pending_frames.get()
        if state.window_exceeded():
            logger.debug("Window exceeded, delaying frame")
            #asyncio.ensure_future(delay_outgoing_data(state, pending, future))
            state.push_iframe(pending, future)
        else:
            i_frame = IFrame.i_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command, False,
                                     state.get_recv_state(), state.get_send_state(), pending.protocol, pending.info)
            ax25.write_packet(i_frame)
            state.sent_frames[state.get_send_state()] = i_frame
            state.futures[state.get_send_state()] = future
            state.inc_send_state()
            state.ack_pending = False
            if not state.t1.running():
                state.t3.cancel()
                state.t1.start()
        state.pending_frames.task_done()
        return AX25StateType.TimerRecovery
    elif event.event_type == AX25EventType.T1_EXPIRE:
        if state.retry_count < 4:
            state.retry_count += 1
            transmit_enquiry(state, ax25)
            return AX25StateType.TimerRecovery
        else:
            logger.debug("datalink retries exceeded, disconnecting")
            if state.get_ack_state() == state.get_send_state():
                ax25.dl_error(state.remote_call, state.local_call, "U")
            else:
                ax25.dl_error(state.remote_call, state.local_call, "I")
            state.internal_event_cb(AX25StateEvent.dl_disconnect(state.remote_call, state.local_call))
            state.clear_pending_iframes()
            dm = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                                UnnumberedType.DM, True)
            ax25.write_packet(dm)
            return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_SABM:
        u_frame = cast(UFrame, event.packet)
        ua = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response,
                            UnnumberedType.UA, u_frame.poll_final)
        ax25.write_packet(ua)
        ax25.dl_error(state.remote_call, state.local_call, "F")
        if not state.get_send_state() == state.get_ack_state():
            state.clear_pending_iframes()
            ax25.dl_connect_indication(state.remote_call, state.local_call)
        state.reset()
        state.t3.start()
        return AX25StateType.Connected
    elif event.event_type == AX25EventType.AX25_RNR:
        # TODO Set peer busy
        return AX25StateType.TimerRecovery
    elif event.event_type == AX25EventType.AX25_RR:
        # TODO Set peer clear
        s_frame = cast(SFrame, event.packet)
        if s_frame.get_command() == SupervisoryCommand.Response and s_frame.poll_final:
            """
            Check if N(R) (received seq) is leq the V(S) (send seq) and if
            the V(A) (ack'd seq) is leq N(R) (received seq).
            
            N(S) is the senders seq number
            N(R) is the receivers next expected seq number
            V(A) is the last acknowledged seq
            V(S) is the next send seq
            V(R) is the next expected seq number
            
            E.g., V(A) <= N(R) <= V(S) reads as:
            if the last acknowledged sequence is less than or equal to the next expected seq is less than
            or equal to the next send seq
            
            We get an RR with N(R) of 5, that means the receiver expects our next send seq to be 5
            If this is equal to our last ack'd seq it means we've missed a whole window.
            """
            state.t1.cancel()
            select_t1_value(state)
            if check_nr(state, s_frame.receive_seq_number):
                state.set_ack_state(s_frame.receive_seq_number)
                if state.get_send_state() == state.get_ack_state():
                    state.t3.start()
                    logger.debug("Re-connected")
                    return AX25StateType.Connected
                else:
                    logger.debug(f"Invoke retransmission, N(R)={state.get_recv_state()}")
                    invoke_retransmission(state, ax25)
                    return AX25StateType.TimerRecovery
            else:
                logger.warning(f"N(R) error recovery, V(S)={state.get_send_state()} N(R)={s_frame.receive_seq_number} "
                               f"V(A)={state.get_ack_state()}")
                nr_error_recovery(state, ax25)
                return AX25StateType.AwaitingConnection
        else:
            if s_frame.get_command() == SupervisoryCommand.Command and s_frame.poll_final:
                enquiry_response(state, ax25)
            if check_nr(state, state.get_recv_state()):
                state.set_ack_state(s_frame.receive_seq_number)
                logger.debug("Still in timer recovery")
                return AX25StateType.TimerRecovery
            else:
                logger.warning(f"N(R) error recovery, V(S)={state.get_send_state()} V(R)={state.get_recv_state()} "
                               f"V(A)={state.get_ack_state()}")
                nr_error_recovery(state, ax25)
                return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_DISC:
        u_frame = cast(UFrame, event.packet)
        state.clear_pending_iframes()
        ua = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response,
                            UnnumberedType.UA, u_frame.poll_final)
        ax25.write_packet(ua)
        ax25.dl_disconnect_indication(state.remote_call, state.local_call)
        state.t1.cancel()
        state.t3.cancel()
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_UA:
        ax25.dl_error(state.remote_call, state.local_call, "C")
        establish_data_link(state, ax25)
        state.layer_3 = False
        return AX25StateType.AwaitingConnection
    elif event.event_type == AX25EventType.AX25_UI:
        ui_frame = cast(UIFrame, event.packet)
        check_ui(ui_frame, ax25)
        if ui_frame.poll_final:
            enquiry_response(state, ax25)
        return AX25StateType.TimerRecovery
    elif event.event_type == AX25EventType.DL_UNIT_DATA:
        pending = cast(InternalInfo, event.packet)
        ui = UIFrame.ui_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command, False,
                              pending.protocol, pending.info)
        ax25.write_packet(ui)
        return AX25StateType.TimerRecovery
    elif event.event_type == AX25EventType.AX25_DM:
        ax25.dl_error(state.remote_call, state.local_call, "E")
        ax25.dl_disconnect_indication(state.remote_call, state.local_call)
        state.clear_pending_iframes()
        state.t1.cancel()
        state.t3.cancel()
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_INFO:
        i_frame = cast(IFrame, event.packet)
        if i_frame.get_command() == SupervisoryCommand.Command:
            if check_nr(state, i_frame.receive_seq_number):
                check_iframe_ack(state, i_frame.receive_seq_number)
                if i_frame.send_seq_number == state.get_recv_state():
                    state.inc_recv_state()
                    state.reject_exception = False
                    ax25.dl_data_indication(state.remote_call, state.local_call, i_frame.protocol, i_frame.info)
                    if i_frame.poll:
                        state.enqueue_info_ack(ax25)
                else:
                    if state.reject_exception:
                        if i_frame.poll:
                            rr = SFrame.s_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response,
                                                SupervisoryType.RR, state.get_recv_state(), True)
                            ax25.write_packet(rr)
                            state.ack_pending = False
                    else:
                        state.reject_exception = True
                        rr = SFrame.s_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Response,
                                            SupervisoryType.REJ, state.get_recv_state(), i_frame.poll)
                        ax25.write_packet(rr)
                        state.ack_pending = False
                return AX25StateType.TimerRecovery
            else:
                logger.warning(f"N(R) error recovery, N(R)={i_frame.receive_seq_number} V(S)={state.get_send_state()}")
                nr_error_recovery(state, ax25)
                return AX25StateType.AwaitingConnection
        else:
            ax25.dl_error(state.remote_call, state.local_call, "S")
            return AX25StateType.TimerRecovery
    elif event.event_type == AX25EventType.AX25_FRMR:
        ax25.dl_error(state.remote_call, state.local_call, "K")
        establish_data_link(state, ax25)
        return AX25StateType.AwaitingConnection
    else:
        logger.debug(f"Ignoring {event}")
        return AX25StateType.TimerRecovery


def awaiting_release_handler(
        state: AX25State,
        event: AX25StateEvent,
        ax25: AX25,
        logger: LoggingMixin) -> AX25StateType:
    assert state.current_state == AX25StateType.AwaitingRelease
    if event.event_type == AX25EventType.DL_DISCONNECT:
        dm = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                            UnnumberedType.DM, False)
        ax25.write_packet(dm)
        return AX25StateType.Disconnected
    elif event.event_type == AX25EventType.AX25_SABM:
        u_frame = cast(UFrame, event.packet)
        dm = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                            UnnumberedType.DM, u_frame.poll_final)
        ax25.write_packet(dm)
        return AX25StateType.AwaitingRelease
    elif event.event_type == AX25EventType.AX25_DISC:
        u_frame = cast(UFrame, event.packet)
        dm = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                            UnnumberedType.DM, u_frame.poll_final)
        ax25.write_packet(dm)
        return AX25StateType.AwaitingRelease
    elif event.event_type == AX25EventType.DL_DATA:
        event.future.set_exception(RuntimeError("Not connected"))
        return AX25StateType.AwaitingRelease
    elif event.event_type == AX25EventType.DL_UNIT_DATA:
        pending = cast(InternalInfo, event.packet)
        ui = UIFrame.ui_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                              False, pending.protocol, pending.info)
        ax25.write_packet(ui)
        return AX25StateType.AwaitingRelease
    elif event.event_type in (AX25EventType.AX25_INFO, AX25EventType.AX25_RR, AX25EventType.AX25_RNR,
                              AX25EventType.AX25_REJ,  AX25EventType.AX25_SREJ):
        u_frame = cast(UFrame, event.packet)
        if u_frame.poll_final:
            dm = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                                UnnumberedType.DM, True)
            ax25.write_packet(dm)
        return AX25StateType.AwaitingRelease
    elif event.event_type == AX25EventType.AX25_UI:
        ui = cast(UIFrame, event.packet)
        check_ui(ui, ax25)
        if ui.poll_final:
            dm = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                                UnnumberedType.DM, True)
            ax25.write_packet(dm)
        return AX25StateType.AwaitingRelease
    elif event.event_type == AX25EventType.AX25_UA:
        ua = cast(UFrame, event.packet)
        if ua.poll_final:
            ax25.dl_disconnect_indication(state.remote_call, state.local_call)
            state.t1.cancel()
            return AX25StateType.Disconnected
        else:
            ax25.dl_error(state.remote_call, state.local_call, "D")
            return AX25StateType.AwaitingRelease
    elif event.event_type == AX25EventType.AX25_DM:
        ua = cast(UFrame, event.packet)
        if ua.poll_final:
            ax25.dl_disconnect_indication(state.remote_call, state.local_call)
            state.t1.cancel()
            return AX25StateType.Disconnected
        else:
            return AX25StateType.AwaitingRelease
    elif event.event_type == AX25EventType.T1_EXPIRE:
        if state.retry_count < 4:
            state.retry_count += 1
            disc = UFrame.u_frame(state.remote_call, state.local_call, [], SupervisoryCommand.Command,
                                  UnnumberedType.DISC, True)
            ax25.write_packet(disc)
            select_t1_value(state)
            state.t1.start()
            return AX25StateType.AwaitingRelease
        else:
            ax25.dl_error(state.remote_call, state.local_call, "H")
            ax25.dl_disconnect_indication(state.remote_call, state.local_call)
            return AX25StateType.Disconnected
    else:
        logger.debug(f"Ignoring {event}")
        return AX25StateType.AwaitingRelease


class DeferredAX25(AX25):
    """
    This is needed to capture calls to the data-link manager within the state machine. This way we can queue
    up the calls and defer calling them until the state machine has completed one full cycle. Otherwise when
    we call _out of_ the state machine (like dl_connect) it may induce further calls _into_ the state machine
    that may result in out of order internal events.
    """
    def __init__(self, actual_ax25: AX25):
        self.actual_ax25: AX25 = actual_ax25
        self.calls = []

    def dl_error(self, remote_call: AX25Call, local_call: AX25Call, error_code):
        self.calls.append(partial(self.actual_ax25.dl_error, remote_call, local_call, error_code))

    def dl_data_indication(self, remote_call: AX25Call, local_call: AX25Call, protocol: L3Protocol, data: bytes):
        self.calls.append(partial(self.actual_ax25.dl_data_indication, remote_call, local_call, protocol, data))

    def dl_connect_indication(self, remote_call: AX25Call, local_call: AX25Call):
        self.calls.append(partial(self.actual_ax25.dl_connect_indication, remote_call, local_call))

    def dl_disconnect_indication(self, remote_call: AX25Call, local_call: AX25Call):
        self.calls.append(partial(self.actual_ax25.dl_disconnect_indication, remote_call, local_call))

    def write_packet(self, packet: AX25Packet):
        self.calls.append(partial(self.actual_ax25.write_packet, packet))

    def local_call(self) -> AX25Call:
        return self.actual_ax25.local_call()

    def apply(self):
        for deferred in self.calls:
            deferred()

    def size(self):
        return len(self.calls)


class AX25StateMachine:
    """State management for AX.25 Data Links

    Holds a mapping of AX.25 sessions keyed on remote callsign.

    """
    def __init__(self, ax25: AX25, timer_factory: Callable[[float, Callable[[], None]], Timer]):
        self._ax25 = ax25
        self._sessions: Dict[Tuple[AX25Call, AX25Call], AX25State] = {}  # (remote, local)
        self._handlers = {
            AX25StateType.Disconnected: disconnected_handler,
            AX25StateType.Connected: connected_handler,
            AX25StateType.AwaitingConnection: awaiting_connection_handler,
            AX25StateType.TimerRecovery: timer_recovery_handler,
            AX25StateType.AwaitingRelease: awaiting_release_handler
        }
        self._timer_factory = timer_factory
        self._logger = logging.getLogger("ax25.state")

    def log(self, state: AX25State, msg: str, *args, **kwargs):
        self._logger.info(f"[Id={state.session_id} Local={state.local_call} Remote={state.remote_call} "
                          f"State={state.current_state}] V(A)={state.get_ack_state()} V(R)={state.get_recv_state()} "
                          f"V(S)={state.get_send_state()} {msg}")

    def _get_or_create_session(self, remote_call: AX25Call, local_call: AX25Call) -> AX25State:
        state = self._sessions.get((remote_call, local_call))
        if state is None:
            state = AX25State.create(remote_call, local_call, self.handle_internal_event, self._timer_factory)
            self._sessions[(remote_call, local_call)] = state
        return state

    def get_sessions(self) -> Dict[AX25Call, AX25StateType]:
        return {s.remote_call: s.current_state for k, s in self._sessions.items() if k[1] == self._ax25.local_call()}

    def get_state(self, remote_call: AX25Call) -> AX25StateType:
        local_call = self._ax25.local_call()
        state = self._sessions.get((remote_call, local_call))
        if state is None:
            return AX25StateType.Disconnected
        else:
            return state.current_state

    def is_window_exceeded(self, remote_call: AX25Call) -> bool:
        local_call = self._ax25.local_call()
        state = self._sessions.get((remote_call, local_call))
        if state is None:
            return False
        else:
            return state.window_exceeded()

    def handle_packet(self, packet: AX25Packet):
        state = self._get_or_create_session(packet.source, packet.dest)
        event = AX25StateEvent.from_packet(packet)
        handler = self._handlers[state.current_state]
        if handler is None:
            raise RuntimeError(f"No handler for {handler}")
        deferred = DeferredAX25(self._ax25)
        logger = LoggingMixin(self._logger, state.log_prefix)
        new_state = handler(state, event, deferred, logger)
        state.current_state = new_state
        logger.debug(f"Handled {event}")
        deferred.apply()

    def handle_internal_event(self, event: AX25StateEvent) -> bool:
        if event.event_type in (AX25EventType.DL_CONNECT, AX25EventType.DL_UNIT_DATA):
            #  allow these events to create a new session
            state = self._get_or_create_session(event.remote_call, self._ax25.local_call())
        else:
            local_call = self._ax25.local_call()
            state = self._sessions.get((event.remote_call, local_call))
        if not state:
            raise RuntimeError(f"No session for internal event {event}")
        handler = self._handlers[state.current_state]
        if handler is None:
            raise RuntimeError(f"No handler for {handler}")
        deferred = DeferredAX25(self._ax25)
        logger = LoggingMixin(self._logger, state.log_prefix)
        new_state = handler(state, event, deferred, logger)
        state.current_state = new_state
        logger.debug(f"Handled {event}")
        deferred.apply()
        return state.window_exceeded()
