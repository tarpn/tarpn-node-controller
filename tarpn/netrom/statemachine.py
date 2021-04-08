import asyncio
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import partial
from itertools import cycle
import logging
from typing import Optional, cast, Dict, Callable

from asyncio import Future

from tarpn.ax25 import AX25Call
from tarpn.log import LoggingMixin
from tarpn.netrom import NetRomPacket, NetRomConnectRequest, NetRomConnectAck, OpType, NetRom, NetRomInfo
from tarpn.util import Timer, chunks


class NetRomEventType(Enum):
    # Packet events
    NETROM_CONNECT = auto()
    NETROM_CONNECT_ACK = auto()
    NETROM_DISCONNECT = auto()
    NETROM_DISCONNECT_ACK = auto()
    NETROM_INFO = auto()
    NETROM_INFO_ACK = auto()

    # API events
    NL_CONNECT = auto()
    NL_DISCONNECT = auto()
    NL_DATA = auto()

    def __repr__(self):
        return self.name

    def __str__(self):
        return self.name


@dataclass
class NetRomStateEvent:
    circuit_id: int
    remote_call: AX25Call
    event_type: NetRomEventType
    packet: Optional[NetRomPacket] = None
    data: Optional[bytes] = None
    mtu: int = 0    # NL_DATA only
    future: Optional[Future] = None
    origin_node: Optional[AX25Call] = None  # NL_CONNECT only
    origin_user: Optional[AX25Call] = None  # NL_CONNECT only

    def __repr__(self):
        return f"{self.event_type}"

    @classmethod
    def from_packet(cls, packet: NetRomPacket):
        if packet.op_type() == OpType.ConnectRequest:
            return NetRomStateEvent(packet.circuit_id, packet.source, NetRomEventType.NETROM_CONNECT, packet)
        elif packet.op_type() == OpType.ConnectAcknowledge:
            return NetRomStateEvent(packet.circuit_id, packet.source, NetRomEventType.NETROM_CONNECT_ACK, packet)
        elif packet.op_type() == OpType.Information:
            info = cast(NetRomInfo, packet)
            return NetRomStateEvent(packet.circuit_id, packet.source, NetRomEventType.NETROM_INFO, packet, info.info)
        elif packet.op_type() == OpType.InformationAcknowledge:
            return NetRomStateEvent(packet.circuit_id, packet.source, NetRomEventType.NETROM_INFO_ACK, packet)
        elif packet.op_type() == OpType.DisconnectRequest:
            return NetRomStateEvent(packet.circuit_id, packet.source, NetRomEventType.NETROM_DISCONNECT, packet)
        elif packet.op_type() == OpType.DisconnectAcknowledge:
            return NetRomStateEvent(packet.circuit_id, packet.source, NetRomEventType.NETROM_DISCONNECT_ACK, packet)
        else:
            raise RuntimeError(f"Cannot create event for {packet}")

    @classmethod
    def nl_connect(cls, circuit_id: int, dest: AX25Call, source: AX25Call, origin_node: AX25Call, origin_user: AX25Call):
        dummy = NetRomPacket.dummy(dest, source)
        event = NetRomStateEvent(circuit_id, dest, NetRomEventType.NL_CONNECT, dummy)
        event.origin_node = origin_node
        event.origin_user = origin_user
        return event

    @classmethod
    def nl_data(cls, circuit_id: int, dest: AX25Call, data: bytes, mtu: int = 0):
        return NetRomStateEvent(circuit_id, dest, NetRomEventType.NL_DATA, None, data, mtu=mtu)

    @classmethod
    def nl_disconnect(cls, circuit_id: int, dest: AX25Call, source: AX25Call):
        dummy = NetRomPacket.dummy(dest, source)
        return NetRomStateEvent(circuit_id, dest, NetRomEventType.NL_DISCONNECT, dummy)


class NetRomStateType(Enum):
    AwaitingConnection = auto()
    Connected = auto()
    AwaitingRelease = auto()
    Disconnected = auto()

    def __repr__(self):
        return self.name

    def __str__(self):
        return self.name


@dataclass
class NetRomCircuit:
    circuit_id: int
    circuit_idx: int
    remote_call: AX25Call
    local_call: AX25Call

    origin_node: AX25Call
    origin_user: AX25Call

    remote_circuit_id: Optional[int] = None
    remote_circuit_idx: Optional[int] = None
    window_size: Optional[int] = None
    ack_future: Optional[Future] = None

    vs: int = 0  # Local send state
    vr: int = 0  # Local receive state
    hw: int = 0  # High-watermark for acknowledged data
    ack_pending: bool = False
    state: NetRomStateType = NetRomStateType.Disconnected
    timer: Timer = field(default=None, repr=False)

    more: bytes = bytearray()
    sent_info: Dict[int, NetRomInfo] = field(default_factory=dict)
    info_futures: Dict[int, Future] = field(default_factory=dict)

    # TODO need an ack timeout

    def __repr__(self):
        return f"NetRomCircuit(id={self.my_circuit_repr()} rid={self.remote_circuit_repr()} state={self.state} " \
               f"remote={self.remote_call} local={self.local_call})"

    def my_circuit_repr(self):
        return f"{self.circuit_idx:02x}{self.circuit_id:02x}"

    def remote_circuit_repr(self):
        return f"{self.remote_circuit_idx:02x}{self.remote_circuit_id:02x}"

    def log_prefix(self):
        return f"NET/ROM [Circuit={self.circuit_id} RemoteCircuit={self.remote_circuit_id} Local={self.local_call} " \
               f"Remote={self.remote_call} State={self.state} V(R)={self.recv_state()} V(S)={self.send_state()}]"

    @classmethod
    def create(cls, circuit_id: int, remote_call: AX25Call, local_call: AX25Call):
        # TODO how to generate new circuit ids and idx?
        return cls(circuit_id, circuit_id, remote_call, local_call)

    def send_state(self):
        return (self.vs % 128) & 0xff

    def inc_send_state(self):
        self.vs += 1

    def recv_state(self):
        return (self.vr % 128) & 0xff

    def inc_recv_state(self):
        self.vr += 1

    def enqueue_info_ack(self, netrom: NetRom):
        if not self.ack_pending:
            self.ack_pending = True
            self.send_info_ack(netrom)
            #asyncio.create_task(self.send_info_ack(netrom))

        #if self.ack_future is None:
        #    self.ack_future = asyncio.create_task(self.send_info_ack(netrom))
        #    self.ack_pending = True

    def send_info_ack(self, netrom: NetRom):
        #await asyncio.sleep(0.000)  # TODO configure this
        if self.ack_pending:
            info_ack = NetRomPacket(
                self.remote_call,
                self.local_call,
                7,  # TODO configure
                self.remote_circuit_idx,
                self.remote_circuit_id,
                0,  # Unused
                self.recv_state(),
                OpType.InformationAcknowledge.as_op_byte(False, False, False)  # or F, T, F ?
            )
            # TODO need to track ack high watermark
            netrom.write_packet(info_ack)
            self.ack_pending = False


def disconnected_handler(
        circuit: NetRomCircuit,
        event: NetRomStateEvent,
        netrom: NetRom,
        logger: LoggingMixin) -> NetRomStateType:
    assert circuit.state == NetRomStateType.Disconnected

    if event.event_type == NetRomEventType.NETROM_CONNECT:
        connect_req = cast(NetRomConnectRequest, event.packet)
        connect_ack = NetRomConnectAck(
            connect_req.source,
            connect_req.dest,
            7,  # TODO get TTL from config
            connect_req.circuit_idx,
            connect_req.circuit_id,
            circuit.circuit_idx,
            circuit.circuit_id,
            OpType.ConnectAcknowledge.as_op_byte(False, False, False),
            connect_req.proposed_window_size
        )
        circuit.remote_circuit_id = connect_req.circuit_id
        circuit.remote_circuit_idx = connect_req.circuit_idx
        if netrom.write_packet(connect_ack):
            netrom.nl_connect_indication(circuit.circuit_idx, circuit.circuit_id,
                                         circuit.remote_call, circuit.local_call,
                                         circuit.origin_node, circuit.origin_user)
            circuit.timer.reset()
            return NetRomStateType.Connected
        else:
            return NetRomStateType.Disconnected
    elif event.event_type in (NetRomEventType.NETROM_CONNECT_ACK, NetRomEventType.NETROM_INFO,
                              NetRomEventType.NETROM_INFO_ACK):
        # If we're disconnected, we don't have the remote circuit's ID/IDX, so we can't really do
        # much here besides try to re-connect
        logger.debug(f"Got unexpected packet {event.packet}. Attempting to reconnect")
        disc = NetRomPacket(
            circuit.remote_call,
            circuit.local_call,
            7,  # TODO configure TTL
            circuit.remote_circuit_idx,
            circuit.remote_circuit_id,
            0,
            0,
            OpType.DisconnectRequest.as_op_byte(False, False, False))
        if netrom.write_packet(disc):
            return NetRomStateType.AwaitingRelease
        else:
            return NetRomStateType.Disconnected
    elif event.event_type == NetRomEventType.NETROM_DISCONNECT_ACK:
        # We are already disconnected, nothing to do here
        return NetRomStateType.Disconnected
    elif event.event_type == NetRomEventType.NETROM_DISCONNECT:
        # Ack this even though we're not connected
        disc_ack = NetRomPacket(
            event.packet.source,
            event.packet.dest,
            7,  # TODO configure TTL
            0,  # Don't know the remote circuit idx
            0,  # Don't know the remote circuit id
            0,  # Our circuit idx
            0,  # Our circuit id
            OpType.DisconnectAcknowledge.as_op_byte(False, False, False)
        )
        netrom.write_packet(disc_ack)
        netrom.nl_disconnect_indication(circuit.circuit_idx, circuit.circuit_id, circuit.remote_call, circuit.local_call)
        circuit.timer.cancel()
        return NetRomStateType.Disconnected
    elif event.event_type == NetRomEventType.NL_CONNECT:
        conn = NetRomConnectRequest(
            circuit.remote_call,
            circuit.local_call,
            7,  # TODO configure TTL
            circuit.circuit_idx,
            circuit.circuit_id,
            0,  # Send no circuit idx
            0,  # Send no circuit id
            OpType.ConnectRequest.as_op_byte(False, False, False),
            2,  # Proposed window size (TODO get this from config)
            circuit.origin_user,  # Origin user
            circuit.origin_node,  # Origin node
        )
        if netrom.write_packet(conn):
            circuit.timer.start()
            return NetRomStateType.AwaitingConnection
        else:
            return NetRomStateType.Disconnected
    elif event.event_type == NetRomEventType.NL_DISCONNECT:
        return NetRomStateType.Disconnected
    elif event.event_type == NetRomEventType.NL_DATA:
        logger.debug("Ignoring unexpected NL_DATA event in disconnected state")
        return NetRomStateType.Disconnected


def awaiting_connection_handler(
        circuit: NetRomCircuit,
        event: NetRomStateEvent,
        netrom: NetRom,
        logger: LoggingMixin) -> NetRomStateType:
    assert circuit.state == NetRomStateType.AwaitingConnection

    if event.event_type == NetRomEventType.NETROM_CONNECT:
        return NetRomStateType.AwaitingConnection
    elif event.event_type == NetRomEventType.NETROM_CONNECT_ACK:
        ack = cast(NetRomConnectAck, event.packet)
        if ack.circuit_idx == circuit.circuit_idx and ack.circuit_id == circuit.circuit_id:
            circuit.remote_circuit_idx = ack.tx_seq_num
            circuit.remote_circuit_id = ack.rx_seq_num
            circuit.window_size = ack.accept_window_size
            netrom.nl_connect_indication(circuit.circuit_idx, circuit.circuit_id,
                                         circuit.remote_call, circuit.local_call,
                                         circuit.origin_node, circuit.origin_user)
            circuit.timer.reset()
            return NetRomStateType.Connected
        else:
            logger.debug("Unexpected circuit id in connection ack")
            return NetRomStateType.AwaitingConnection
    elif event.event_type in (NetRomEventType.NETROM_DISCONNECT, NetRomEventType.NETROM_DISCONNECT_ACK,
                              NetRomEventType.NETROM_INFO, NetRomEventType.NETROM_INFO_ACK):
        return NetRomStateType.AwaitingConnection
    elif event.event_type == NetRomEventType.NL_CONNECT:
        conn = NetRomConnectRequest(
            circuit.remote_call,
            circuit.local_call,
            7,  # TODO configure TTL
            circuit.circuit_idx,
            circuit.circuit_id,
            0,  # Unused
            0,  # Unused
            OpType.ConnectRequest.as_op_byte(False, False, False),
            2,  # TODO get this from config
            circuit.origin_user,  # Origin user
            circuit.origin_node  # Origin node
        )
        netrom.write_packet(conn)
        circuit.timer.reset()
        return NetRomStateType.AwaitingConnection
    elif event.event_type in (NetRomEventType.NL_DISCONNECT, NetRomEventType.NL_DATA):
        return NetRomStateType.AwaitingConnection


def connected_handler(
        circuit: NetRomCircuit,
        event: NetRomStateEvent,
        netrom: NetRom,
        logger: LoggingMixin) -> NetRomStateType:
    assert circuit.state == NetRomStateType.Connected

    if event.event_type == NetRomEventType.NETROM_CONNECT:
        connect_req = cast(NetRomConnectRequest, event.packet)
        if connect_req.circuit_idx == circuit.circuit_idx and connect_req.circuit_id == circuit.circuit_id:
            # Treat this as a reconnect and ack it
            connect_ack = NetRomConnectAck(
                connect_req.source,
                connect_req.dest,
                7,  # TODO get TTL from config
                connect_req.circuit_idx,
                connect_req.circuit_id,
                circuit.circuit_idx,
                circuit.circuit_id,
                OpType.ConnectAcknowledge.as_op_byte(False, False, False),
                connect_req.proposed_window_size
            )
            netrom.write_packet(connect_ack)
            netrom.nl_connect_indication(circuit.circuit_idx, circuit.circuit_id,
                                         circuit.remote_call, circuit.local_call,
                                         circuit.origin_node, circuit.origin_user)
            circuit.timer.reset()
            return NetRomStateType.Connected
        else:
            # Reject this and disconnect
            logger.debug("Rejecting connect request due to invalid circuit ID/IDX")
            connect_rej = NetRomConnectAck(
                connect_req.source,
                connect_req.dest,
                7,  # TODO get TTL from config
                connect_req.circuit_idx,
                connect_req.circuit_id,
                circuit.circuit_idx,
                circuit.circuit_id,
                OpType.ConnectAcknowledge.as_op_byte(True, False, False),
                connect_req.proposed_window_size
            )
            netrom.write_packet(connect_rej)
            netrom.nl_disconnect_indication(circuit.circuit_idx, circuit.circuit_id, circuit.remote_call, circuit.local_call)
            circuit.timer.cancel()
            return NetRomStateType.Disconnected
    elif event.event_type == NetRomEventType.NETROM_CONNECT_ACK:
        connect_ack = cast(NetRomConnectAck, event.packet)
        if connect_ack.tx_seq_num == circuit.remote_circuit_idx and \
                connect_ack.rx_seq_num == circuit.remote_circuit_id and \
                connect_ack.circuit_idx == circuit.circuit_idx and \
                connect_ack.circuit_id == circuit.circuit_id:
            netrom.nl_connect_indication(circuit.circuit_idx, circuit.circuit_id,
                                         circuit.remote_call, circuit.local_call,
                                         circuit.origin_node, circuit.origin_user)
            circuit.timer.reset()
            return NetRomStateType.Connected
        else:
            #  TODO what now?
            return NetRomStateType.Connected
    elif event.event_type == NetRomEventType.NETROM_DISCONNECT:
        disc_ack = NetRomPacket(
            event.packet.source,
            event.packet.dest,
            7,  # TODO configure TTL
            circuit.remote_circuit_id,
            circuit.remote_circuit_id,
            0,  # Not used
            0,  # Not used
            OpType.DisconnectAcknowledge.as_op_byte(False, False, False)
        )
        netrom.write_packet(disc_ack)
        netrom.nl_disconnect_indication(circuit.circuit_idx, circuit.circuit_id, circuit.remote_call, circuit.local_call)
        circuit.timer.cancel()
        return NetRomStateType.Disconnected
    elif event.event_type == NetRomEventType.NETROM_DISCONNECT_ACK:
        logger.debug("Unexpected disconnect ack in connected state!")
        return NetRomStateType.Disconnected
    elif event.event_type == NetRomEventType.NETROM_INFO:
        """
        The TX number from the INFO packet is the current sequence number while the the RX number is the next
        expected sequence number on the other end of the circuit. This serves as a mechanism to acknowledge
        previous INFO without sending an explicit ACK
        """
        info = cast(NetRomInfo, event.packet)
        if info.tx_seq_num == circuit.recv_state():
            # We got the message number we expected
            circuit.inc_recv_state()
            circuit.enqueue_info_ack(netrom)
            circuit.more += info.info
            if not info.more_follows():
                # TODO expire old more-follows data
                netrom.nl_data_indication(circuit.circuit_idx, circuit.circuit_id, circuit.remote_call,
                                          circuit.local_call, circuit.more)
                circuit.more = bytearray()
        elif info.tx_seq_num < circuit.recv_state():
            # Possible retransmission of previous message, ignore?
            pass
        else:
            # Got a higher number than expected, we missed something, ask the sender to rewind
            # to our last confirmed state
            nak = NetRomPacket(
                info.source,
                info.dest,
                7,  # TODO config
                circuit.remote_circuit_idx,
                circuit.remote_circuit_id,
                0,  # Unused
                circuit.recv_state(),
                OpType.InformationAcknowledge.as_op_byte(False, True, False)
            )
            netrom.write_packet(nak)

        # Handle the ack logic
        if info.rx_seq_num > circuit.hw:
            for seq in range(info.rx_seq_num, circuit.hw + 1):
                seq = (seq + 127) % 128
                circuit.info_futures[seq].set_result(True)
            circuit.hw = info.rx_seq_num
        else:
            # Out of sync, error
            pass

        if info.rx_seq_num == circuit.send_state():
            # We are in-sync, all is well
            pass
        elif info.rx_seq_num < circuit.send_state():
            # Other side is lagging
            pass
        else:
            # Other side has ack'd something out of range, raise an error
            pass

        # Handle the other flags
        if info.choke():
            # TODO stop sending until further notice
            pass
        if info.nak():
            seq_resend = event.packet.rx_seq_num
            logger.warning(f"Got Info NAK, rewinding to {seq_resend}")
            while seq_resend < circuit.send_state():
                info_to_resend = circuit.sent_info[seq_resend]
                info_to_resend.rx_seq_num = circuit.recv_state()
                netrom.write_packet(info_to_resend)
                seq_resend += 1

        return NetRomStateType.Connected
    elif event.event_type == NetRomEventType.NETROM_INFO_ACK:
        """
        If the choke flag is set (bit 7 of the opcode byte), it indicates that this node cannot accept any more 
        information messages until further notice. If the NAK flag is set (bit 6 of the opcode byte), it indicates that 
        a selective retransmission of the frame identified by the Rx Sequence Number is being requested.
        """
        ack = event.packet

        if ack.rx_seq_num > circuit.hw:
            for seq in range(ack.rx_seq_num, circuit.hw + 1):
                seq = (seq + 127) % 128
                fut = circuit.info_futures.get(seq)
                if fut:
                    fut.set_result(True)
            circuit.hw = ack.rx_seq_num
        else:
            # Out of sync, error
            pass

        if ack.rx_seq_num == circuit.send_state():
            seq = (ack.rx_seq_num + 127) % 128
            fut = circuit.info_futures.get(seq)
            if fut:
                fut.set_result(True)
        elif ack.rx_seq_num < circuit.send_state():
            # Lagging behind
            pass
        else:
            # Invalid state, error
            pass

        if ack.choke():
            logger.warning("Got Info Choke")
            # TODO stop sending until further notice
            pass
        if ack.nak():
            seq_resend = event.packet.rx_seq_num
            logger.warning(f"Got Info NAK, rewinding to {seq_resend}")
            while seq_resend < circuit.send_state():
                info_to_resend = circuit.sent_info[seq_resend]
                info_to_resend.rx_seq_num = circuit.recv_state()
                netrom.write_packet(info_to_resend)
                seq_resend += 1
        elif event.packet.rx_seq_num != circuit.send_state():
            logger.warning("Info sync error")
            # Out of sync, what now? Update circuit send state?
            pass
        return NetRomStateType.Connected
    elif event.event_type == NetRomEventType.NL_CONNECT:
        conn = NetRomConnectRequest(
            circuit.remote_call,
            circuit.local_call,
            7,  # TODO configure TTL
            circuit.circuit_idx,
            circuit.circuit_id,
            0,  # Unused
            0,  # Unused
            OpType.ConnectRequest.as_op_byte(False, False, False),
            2,  # TODO get this from config
            circuit.origin_user,  # Origin user
            circuit.origin_node,  # Origin node
        )
        netrom.write_packet(conn)
        circuit.timer.reset()
        return NetRomStateType.AwaitingConnection
    elif event.event_type == NetRomEventType.NL_DISCONNECT:
        disc = NetRomPacket(
            circuit.remote_call,
            circuit.local_call,
            7,  # TODO configure TTL
            circuit.remote_circuit_idx,
            circuit.remote_circuit_id,
            0,
            0,
            OpType.DisconnectRequest.as_op_byte(False, False, False))
        netrom.write_packet(disc)
        circuit.timer.cancel()
        return NetRomStateType.AwaitingRelease
    elif event.event_type == NetRomEventType.NL_DATA:
        if event.mtu > 0:
            fragments = list(chunks(event.data, event.mtu))
            for fragment in fragments[:-1]:
                info = NetRomInfo(
                    circuit.remote_call,
                    circuit.local_call,
                    7,  # TODO
                    circuit.remote_circuit_idx,
                    circuit.remote_circuit_id,
                    circuit.send_state(),
                    circuit.recv_state(),
                    OpType.Information.as_op_byte(False, False, True),
                    fragment
                )
                netrom.write_packet(info)
                circuit.sent_info[info.tx_seq_num] = info
                circuit.info_futures[info.tx_seq_num] = event.future
                circuit.inc_send_state()
            last = fragments[-1]
        else:
            last = event.data
        info = NetRomInfo(
            circuit.remote_call,
            circuit.local_call,
            7,  # TODO
            circuit.remote_circuit_idx,
            circuit.remote_circuit_id,
            circuit.send_state(),
            circuit.recv_state(),
            OpType.Information.as_op_byte(False, False, False),
            last
        )
        netrom.write_packet(info)
        circuit.sent_info[info.tx_seq_num] = info
        circuit.info_futures[info.tx_seq_num] = event.future
        circuit.inc_send_state()

        async def check_ack():
            # TODO need to implement timeout and retry
            await asyncio.sleep(10.000)
            if circuit.hw <= info.tx_seq_num:
                # Retransmit
                print(f"Retransmit {info}")
        #asyncio.create_task(check_ack())

        return NetRomStateType.Connected


def awaiting_release_handler(
        circuit: NetRomCircuit,
        event: NetRomStateEvent,
        netrom: NetRom,
        logger: LoggingMixin) -> NetRomStateType:
    assert circuit.state == NetRomStateType.AwaitingRelease

    if event.event_type == NetRomEventType.NETROM_DISCONNECT_ACK:
        if event.packet.circuit_idx == circuit.circuit_idx and event.packet.circuit_id == circuit.circuit_id:
            netrom.nl_disconnect_indication(circuit.circuit_idx, circuit.circuit_id, circuit.remote_call, circuit.local_call)
            return NetRomStateType.Disconnected
        else:
            logger.debug("Invalid disconnect ack. Disconnecting anyways")
            return NetRomStateType.Disconnected
    elif event.event_type == NetRomEventType.NETROM_DISCONNECT:
        disc_ack = NetRomPacket(
            event.packet.source,
            event.packet.dest,
            7,  # TODO configure TTL
            event.packet.circuit_idx,
            event.packet.circuit_id,
            0,  # Our circuit idx
            0,  # Our circuit id
            OpType.DisconnectAcknowledge.as_op_byte(False, False, False)
        )
        netrom.write_packet(disc_ack)
        netrom.nl_disconnect_indication(circuit.circuit_idx, circuit.circuit_id, circuit.remote_call, circuit.local_call)
        circuit.timer.cancel()
        return NetRomStateType.Disconnected
    else:
        # TODO handle any other cases differently?
        return NetRomStateType.AwaitingRelease


class NetRomStateMachine:
    def __init__(self, netrom: NetRom, timer_factory: Callable[[float, Callable[[], None]], Timer]):
        self.timer_factory = timer_factory
        self._netrom: NetRom = netrom
        self._circuits: Dict[str, NetRomCircuit] = {}
        self._handlers = {
            NetRomStateType.Disconnected: disconnected_handler,
            NetRomStateType.AwaitingConnection: awaiting_connection_handler,
            NetRomStateType.Connected: connected_handler,
            NetRomStateType.AwaitingRelease: awaiting_release_handler
        }
        self._next_circuit_key_iter = cycle(range(0xffff))
        self._events = asyncio.Queue()
        self._stopped = False
        self._logger = logging.getLogger("netrom.state")

    async def start(self):
        self._logger.info("Start NetRom state machine")
        while not self._stopped:
            await self._loop()

    def stop(self):
        self._stopped = True

    async def _loop(self):
        event = await self._events.get()
        try:
            self._handle_event(event)
        finally:
            self._events.task_done()

    def _next_circuit_id(self) -> int:
        start = next(self._next_circuit_key_iter)
        key = start
        while key in self._circuits.keys():
            key = next(self._next_circuit_key_iter)
            if key == start:
                raise RuntimeError("Ran out of circuits!")
        return key

    def _reap_unused_circuits(self):
        to_reap = []
        for circuit_key, circuit in self._circuits.items():
            if circuit.state == NetRomStateType.Disconnected:
                #  TODO also check last used time
                to_reap.append(circuit_key)
        for circuit_key in to_reap:
            self._logger.debug(f"Reaping disconnected circuit {circuit_key}")
            del self._circuits[circuit_key]

    def _get_or_create_circuit(self, netrom_packet: NetRomPacket) -> NetRomCircuit:
        if isinstance(netrom_packet, NetRomConnectRequest):
            #  self._reap_unused_circuits()
            next_circuit_id = self._next_circuit_id()
            if next_circuit_id == -1:
                return None  # TODO handle this case
            circuit = NetRomCircuit(next_circuit_id, next_circuit_id, netrom_packet.source, netrom_packet.dest,
                                    origin_node=netrom_packet.origin_node, origin_user=netrom_packet.origin_user)
            circuit.timer = self.timer_factory(10_000, partial(self.handle_timeout, next_circuit_id))
            circuit_key = f"{next_circuit_id:02d}:{next_circuit_id:02d}"
            self._circuits[circuit_key] = circuit
        elif isinstance(netrom_packet, NetRomConnectAck):
            conn_ack = cast(NetRomConnectAck, netrom_packet)
            circuit_key = f"{conn_ack.circuit_idx:02d}:{conn_ack.circuit_id:02d}"
            circuit = self._circuits[circuit_key]
            circuit.remote_circuit_idx = conn_ack.tx_seq_num
            circuit.remote_circuit_id = conn_ack.rx_seq_num
        else:
            circuit_key = f"{netrom_packet.circuit_idx:02d}:{netrom_packet.circuit_id:02d}"
            if circuit_key in self._circuits:
                circuit = self._circuits[circuit_key]
            else:
                self._logger.warning(f"Creating new circuit for packet {netrom_packet}")
                # TODO should return an error to peer
                circuit = NetRomCircuit(netrom_packet.circuit_id, netrom_packet.circuit_idx,
                                        netrom_packet.source, netrom_packet.dest,
                                        None, None)
                circuit.timer = self.timer_factory(10_000, partial(self.handle_timeout, netrom_packet.circuit_idx))
                self._circuits[circuit_key] = circuit
        return circuit

    def _get_circuit(self, circuit_idx: int, circuit_id: int) -> NetRomCircuit:
        circuit_key = f"{circuit_idx:02d}:{circuit_id:02d}"
        circuit = self._circuits[circuit_key]
        return circuit

    def get_circuits(self):
        return [circuit.circuit_id for circuit in self._circuits.values()]

    def get_state(self, circuit_id: int):
        return self._get_circuit(circuit_id, circuit_id).state

    def handle_packet(self, packet: NetRomPacket):
        circuit = self._get_or_create_circuit(packet)
        event = NetRomStateEvent.from_packet(packet)
        event.circuit_id = circuit.circuit_id
        asyncio.create_task(self._events.put(event))

    def handle_packet_sync(self, packet: NetRomPacket):
        circuit = self._get_or_create_circuit(packet)
        event = NetRomStateEvent.from_packet(packet)
        event.circuit_id = circuit.circuit_id
        self._handle_event(event)

    def _handle_event(self, event: NetRomStateEvent):
        if event is not None:
            circuit = self._get_circuit(event.circuit_id, event.circuit_id)
            handler = self._handlers[circuit.state]
            if handler is None:
                raise RuntimeError(f"No handler for state {handler}")
            logger = LoggingMixin(self._logger, circuit.log_prefix)
            try:
                new_state = handler(circuit, event, self._netrom, logger)
                circuit.state = new_state
                logger.info(f"Handled {event}")
            except Exception:
                logger.exception(f"Failed to handle {event}")

    def handle_internal_event_sync(self, event: NetRomStateEvent):
        if event.event_type == NetRomEventType.NL_CONNECT:
            if event.circuit_id == -1:
                circuit_id = self._next_circuit_id()
            else:
                circuit_id = event.circuit_id
            event.circuit_id = circuit_id
            circuit = NetRomCircuit(circuit_id, circuit_id, event.remote_call, self._netrom.local_call(),
                                    origin_node=event.origin_node, origin_user=event.origin_user)
            circuit.timer = self.timer_factory(10_000, partial(self.handle_timeout, circuit_id))
            circuit_key = f"{circuit.circuit_idx:02d}:{circuit.circuit_id:02d}"
            self._circuits[circuit_key] = circuit

        self._handle_event(event)

    def handle_internal_event(self, event: NetRomStateEvent):
        if event.event_type == NetRomEventType.NL_CONNECT:
            if event.circuit_id == -1:
                circuit_id = self._next_circuit_id()
            else:
                circuit_id = event.circuit_id
            event.circuit_id = circuit_id
            circuit = NetRomCircuit(circuit_id, circuit_id, event.remote_call, self._netrom.local_call(),
                                    origin_node=event.origin_node, origin_user=event.origin_user)
            circuit.timer = self.timer_factory(10_000, partial(self.handle_timeout, circuit_id))
            circuit_key = f"{circuit.circuit_idx:02d}:{circuit.circuit_id:02d}"
            self._circuits[circuit_key] = circuit

        asyncio.create_task(self._events.put(event))
        return event

    def handle_timeout(self, circuit_id: int):
        pass
