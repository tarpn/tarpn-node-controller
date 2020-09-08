import asyncio
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, cast, Dict

from asyncio import Future

from tarpn.ax25 import AX25Call
from tarpn.netrom import NetRomPacket, NetRomConnectRequest, NetRomConnectAck, OpType, NetRom, NetRomInfo


class NetRomEventType(Enum):
    NETROM_CONNECT = auto()
    NETROM_CONNECT_ACK = auto()
    NETROM_DISCONNECT = auto()
    NETROM_DISCONNECT_ACK = auto()
    NETROM_INFO = auto()
    NETROM_INFO_ACK = auto()

    NL_CONNECT = auto()
    NL_DISCONNECT = auto()
    NL_DATA = auto()


@dataclass
class NetRomStateEvent:
    circuit_id: int
    remote_call: AX25Call
    event_type: NetRomEventType
    packet: Optional[NetRomPacket] = None
    data: Optional[bytes] = None

    @classmethod
    def from_packet(cls, packet: NetRomPacket):
        if packet.op_type() == OpType.ConnectRequest:
            return NetRomStateEvent(packet.circuit_id, packet.origin, NetRomEventType.NETROM_CONNECT, packet)
        elif packet.op_type() == OpType.ConnectAcknowledge:
            return NetRomStateEvent(packet.circuit_id, packet.origin, NetRomEventType.NETROM_CONNECT_ACK, packet)
        elif packet.op_type() == OpType.Information:
            info = cast(NetRomInfo, packet)
            return NetRomStateEvent(packet.circuit_id, packet.origin, NetRomEventType.NETROM_INFO, packet, info.info)
        elif packet.op_type() == OpType.InformationAcknowledge:
            return NetRomStateEvent(packet.circuit_id, packet.origin, NetRomEventType.NETROM_INFO_ACK, packet)
        elif packet.op_type() == OpType.DisconnectRequest:
            return NetRomStateEvent(packet.circuit_id, packet.origin, NetRomEventType.NETROM_DISCONNECT, packet)
        elif packet.op_type() == OpType.DisconnectAcknowledge:
            return NetRomStateEvent(packet.circuit_id, packet.origin, NetRomEventType.NETROM_DISCONNECT_ACK, packet)
        else:
            raise RuntimeError(f"Cannot create event for {packet}")

    @classmethod
    def nl_connect(cls, circuit_id: int, dest: AX25Call, source: AX25Call):
        dummy = NetRomPacket.dummy(dest, source)
        return NetRomStateEvent(circuit_id, dest, NetRomEventType.NL_CONNECT, dummy)

    @classmethod
    def nl_data(cls, circuit_id: int, dest: AX25Call, data: bytes):
        return NetRomStateEvent(circuit_id, dest, NetRomEventType.NL_DATA, None, data)

    @classmethod
    def nl_disconnect(cls, circuit_id: int, dest: AX25Call, source: AX25Call):
        dummy = NetRomPacket.dummy(dest, source)
        return NetRomStateEvent(circuit_id, dest, NetRomEventType.NL_DISCONNECT, dummy)


class NetRomStateType(Enum):
    AwaitingConnection = auto()
    Connected = auto()
    AwaitingRelease = auto()
    Disconnected = auto()


@dataclass
class NetRomCircuit:
    circuit_id: int
    circuit_idx: int
    remote_call: AX25Call
    local_call: AX25Call

    remote_circuit_id: Optional[int] = None
    remote_circuit_idx: Optional[int] = None
    window_size: Optional[int] = None
    ack_future: Optional[Future] = None

    vs: int = 0
    vr: int = 0
    ack_pending: bool = False
    state: NetRomStateType = NetRomStateType.Disconnected

    more: bytes = bytearray()

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
        if self.ack_future is None:
            self.ack_future = asyncio.ensure_future(self.send_info_ack(netrom))
            self.ack_pending = True

    async def send_info_ack(self, netrom: NetRom):
        await asyncio.sleep(0.100)  # TODO configure this
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
            netrom.write_packet(info_ack)
            self.ack_pending = False


def disconnected_handler(
        circuit: NetRomCircuit,
        event: NetRomStateEvent,
        netrom: NetRom) -> NetRomStateType:
    assert circuit.state == NetRomStateType.Disconnected
    print(f"in disconnected state, got: {event}")

    if event.event_type == NetRomEventType.NETROM_CONNECT:
        connect_req = cast(NetRomConnectRequest, event.packet)
        connect_ack = NetRomConnectAck(
            connect_req.origin_node,
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
            netrom.nl_connect(circuit.circuit_idx, circuit.circuit_id, circuit.remote_call, circuit.local_call)
            return NetRomStateType.Connected
        else:
            return NetRomStateType.Disconnected
    elif event.event_type in (NetRomEventType.NETROM_CONNECT_ACK, NetRomEventType.NETROM_DISCONNECT,
                              NetRomEventType.NETROM_DISCONNECT_ACK, NetRomEventType.NETROM_INFO,
                              NetRomEventType.NETROM_INFO_ACK):
        print(f"Got unexpected packet {event.packet}. Responding with disconnect request")
        disc = NetRomPacket(
            event.packet.origin,
            event.packet.dest,
            7,  # TODO configure TTL
            event.packet.circuit_idx,
            event.packet.circuit_id,
            0,  # Send no circuit idx
            0,  # Send no circuit id
            OpType.DisconnectRequest.as_op_byte(False, False, False))
        netrom.write_packet(disc)
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
            circuit.local_call,  # Origin user
            circuit.local_call,  # Origin node
        )
        if netrom.write_packet(conn):
            return NetRomStateType.AwaitingConnection
        else:
            return NetRomStateType.Disconnected
    elif event.event_type == NetRomEventType.NL_DISCONNECT:
        return NetRomStateType.Disconnected
    elif event.event_type == NetRomEventType.NL_DATA:
        print("Ignoring unexpected NL_DATA event in disconnected state")
        return NetRomStateType.Disconnected


def awaiting_connection_handler(
        circuit: NetRomCircuit,
        event: NetRomStateEvent,
        netrom: NetRom) -> NetRomStateType:
    assert circuit.state == NetRomStateType.AwaitingConnection
    print(f"in awaiting connection state, got: {event}")

    if event.event_type == NetRomEventType.NETROM_CONNECT:
        return NetRomStateType.AwaitingConnection
    elif event.event_type == NetRomEventType.NETROM_CONNECT_ACK:
        ack = cast(NetRomConnectAck, event.packet)
        if ack.circuit_idx == circuit.circuit_idx and ack.circuit_id == circuit.circuit_id:
            circuit.remote_circuit_idx = ack.rx_seq_num
            circuit.remote_circuit_id = ack.tx_seq_num
            circuit.window_size = ack.accept_window_size
            netrom.nl_connect(circuit.circuit_idx, circuit.circuit_id, circuit.remote_call, circuit.local_call)
            return NetRomStateType.Connected
        else:
            print("Unexpected circuit id in connection ack")
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
            circuit.local_call,  # Origin user
            circuit.local_call  # Origin node
        )
        netrom.write_packet(conn)
        return NetRomStateType.AwaitingConnection
    elif event.event_type in (NetRomEventType.NL_DISCONNECT, NetRomEventType.NL_DATA):
        return NetRomStateType.AwaitingConnection


def connected_handler(
        circuit: NetRomCircuit,
        event: NetRomStateEvent,
        netrom: NetRom) -> NetRomStateType:
    assert circuit.state == NetRomStateType.Connected
    print(f"in connected state, got: {event}")

    if event.event_type == NetRomEventType.NETROM_CONNECT:
        connect_req = cast(NetRomConnectRequest, event.packet)
        if connect_req.circuit_idx == circuit.circuit_idx and connect_req.circuit_id == circuit.circuit_id:
            # Treat this as a reconnect and ack it
            connect_ack = NetRomConnectAck(
                connect_req.origin_node,
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
            netrom.nl_connect(circuit.circuit_idx, circuit.circuit_id, circuit.remote_call, circuit.local_call)
            return NetRomStateType.Connected
        else:
            # Reject this and disconnect
            print("Rejecting connect request due to invalid circuit ID/IDX")
            connect_rej = NetRomConnectAck(
                connect_req.origin_node,
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
            netrom.nl_disconnect(circuit.circuit_idx, circuit.circuit_id, circuit.remote_call, circuit.local_call)
            return NetRomStateType.Disconnected
    elif event.event_type == NetRomEventType.NETROM_CONNECT_ACK:
        connect_ack = cast(NetRomConnectAck, event.packet)
        if connect_ack.tx_seq_num == circuit.remote_circuit_idx and \
                connect_ack.rx_seq_num == circuit.remote_circuit_id and \
                connect_ack.circuit_idx == circuit.circuit_idx and \
                connect_ack.circuit_id == circuit.circuit_id:
            netrom.nl_connect(circuit.circuit_idx, circuit.circuit_id, circuit.remote_call, circuit.local_call)
            return NetRomStateType.Connected
        else:
            #  TODO what now?
            return NetRomStateType.Connected
    elif event.event_type == NetRomEventType.NETROM_DISCONNECT:
        disc_ack = NetRomPacket(
            event.packet.origin,
            event.packet.dest,
            7,  # TODO configure TTL
            event.packet.circuit_idx,
            event.packet.circuit_id,
            0,  # Our circuit idx
            0,  # Our circuit id
            OpType.DisconnectAcknowledge.as_op_byte(False, False, False)
        )
        netrom.write_packet(disc_ack)
        netrom.nl_disconnect(circuit.circuit_idx, circuit.circuit_id, circuit.remote_call, circuit.local_call)
        return NetRomStateType.Disconnected
    elif event.event_type == NetRomEventType.NETROM_DISCONNECT_ACK:
        print("Unexpected disconnect ack in connected state!")
        return NetRomStateType.Disconnected
    elif event.event_type == NetRomEventType.NETROM_INFO:
        info = cast(NetRomInfo, event.packet)
        # First handle the info and ack it
        if info.tx_seq_num == circuit.recv_state():
            circuit.inc_recv_state()
            circuit.enqueue_info_ack(netrom)
            circuit.more += info.info
            if not info.more_follows():
                # TODO expire old more-follows data
                netrom.nl_data(circuit.circuit_idx, circuit.circuit_id, circuit.remote_call, circuit.local_call, info.info)
                circuit.more = bytearray()
        else:
            nak = NetRomPacket(
                info.origin,
                info.dest,
                7,  # TODO config
                info.circuit_idx,
                info.circuit_id,
                circuit.circuit_idx,
                circuit.circuit_id,
                OpType.InformationAcknowledge.as_op_byte(False, True, False)
            )
            netrom.write_packet(nak)

        # Now, handle the piggybacked info-ack fields
        if event.packet.choke():
            # TODO stop sending until further notice
            pass
        if event.packet.nak():
            info_to_resend = event.packet.rx_seq_num
            # TODO resend this info
            pass
        elif event.packet.rx_seq_num != circuit.send_state():
            # Out of sync, what now? Update circuit send state?
            pass
        return NetRomStateType.Connected
    elif event.event_type == NetRomEventType.NETROM_INFO_ACK:
        """
        If the choke flag is set (bit 7 of the opcode byte), it indicates that this node cannot accept any more 
        information messages until further notice. If the NAK flag is set (bit 6 of the opcode byte), it indicates that 
        a selective retransmission of the frame identified by the Rx Sequence Number is being requested.
        """
        if event.packet.choke():
            # TODO stop sending until further notice
            pass
        if event.packet.nak():
            info_to_resend = event.packet.rx_seq_num
            # TODO resend this info
            pass
        elif event.packet.rx_seq_num != circuit.send_state():
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
            circuit.local_call,  # Origin user
            circuit.local_call,  # Origin node
        )
        netrom.write_packet(conn)
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
        return NetRomStateType.AwaitingRelease
    elif event.event_type == NetRomEventType.NL_DATA:
        info = NetRomInfo(
            circuit.remote_call,
            circuit.local_call,
            7,  # TODO
            circuit.remote_circuit_idx,
            circuit.remote_circuit_id,
            circuit.send_state(),
            circuit.recv_state(),
            OpType.Information.as_op_byte(False, False, False),
            event.data
        )
        netrom.write_packet(info)
        circuit.inc_send_state()
        return NetRomStateType.Connected


def awaiting_release_handler(circuit: NetRomCircuit,
        event: NetRomStateEvent,
        netrom: NetRom) -> NetRomStateType:
    assert circuit.state == NetRomStateType.AwaitingRelease
    print(f"in awaiting release state, got: {event}")

    if event.event_type == NetRomEventType.NETROM_DISCONNECT:
        if event.packet.circuit_idx == circuit.circuit_idx and event.packet.circuit_id == circuit.circuit_id:
            netrom.nl_disconnect(circuit.circuit_idx, circuit.circuit_id, circuit.remote_call, circuit.local_call)
            return NetRomStateType.Disconnected
        else:
            print("Invalid disconnect ack. Disconnecting anyways")
            return NetRomStateType.Disconnected
    else:
        # TODO handle any other cases differently?
        return NetRomStateType.AwaitingRelease


class NetRomStateMachine:
    def __init__(self, netrom: NetRom):
        self._netrom: NetRom = netrom
        self._circuits: Dict[str, NetRomCircuit] = {}
        self._handlers = {
            NetRomStateType.Disconnected: disconnected_handler,
            NetRomStateType.AwaitingConnection: awaiting_connection_handler,
            NetRomStateType.Connected: connected_handler,
            NetRomStateType.AwaitingRelease: awaiting_release_handler
        }
        self._next_circuit_key = 0

    def _next_circuit_id(self) -> int:
        key = (self._next_circuit_key + 1) % 0xffff
        while key in self._circuits.keys():
            key = (self._next_circuit_key + 1) % 0xffff
        if key in self._circuits.keys():
            return -1
        else:
            return key

    def _reap_unused_circuits(self):
        to_reap = []
        for circuit_key, circuit in self._circuits.items():
            if circuit.state == NetRomStateType.Disconnected:
                #  TODO also check last used time
                to_reap.append(circuit_key)
        for circuit_key in to_reap:
            print(f"Reaping disconnected circuit {circuit_key}")
            del self._circuits[circuit_key]

    def _get_or_create_circuit(self, netrom_packet: NetRomPacket) -> NetRomCircuit:
        if isinstance(netrom_packet, NetRomConnectRequest):
            #  self._reap_unused_circuits()
            next_circuit_id = self._next_circuit_id()
            if next_circuit_id == -1:
                return None  # TODO handle this case
            circuit = NetRomCircuit(next_circuit_id, next_circuit_id, netrom_packet.origin, netrom_packet.dest)
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
                circuit = NetRomCircuit(netrom_packet.circuit_idx, netrom_packet.circuit_id, netrom_packet.origin,
                                        netrom_packet.dest)
                self._circuits[circuit_key] = circuit
        return circuit

    def _get_circuit(self, circuit_idx: int, circuit_id: int) -> NetRomCircuit:
        circuit_key = f"{circuit_idx:02d}:{circuit_id:02d}"
        circuit = self._circuits[circuit_key]
        return circuit

    def handle_packet(self, packet: NetRomPacket):
        circuit = self._get_or_create_circuit(packet)
        event = NetRomStateEvent.from_packet(packet)
        handler = self._handlers[circuit.state]
        if handler is None:
            raise RuntimeError(f"No handler for state {handler}")
        new_state = handler(circuit, event, self._netrom)
        circuit.state = new_state

    def handle_internal_event(self, event: NetRomStateEvent):
        if event.event_type == NetRomEventType.NL_CONNECT:
            circuit = NetRomCircuit(event.circuit_id, event.circuit_id, event.remote_call, self._netrom.local_call())
            circuit_key = f"{circuit.circuit_idx:02d}:{circuit.circuit_id:02d}"
            self._circuits[circuit_key] = circuit
        else:
            circuit = self._get_circuit(event.circuit_id, event.circuit_id)
        handler = self._handlers[circuit.state]
        if handler is None:
            raise RuntimeError(f"No handler for state {handler}")
        new_state = handler(circuit, event, self._netrom)
        circuit.state = new_state
