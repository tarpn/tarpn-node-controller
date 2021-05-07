import logging
from dataclasses import dataclass
from typing import Callable, Dict, Tuple, List, Any, Set, Optional

from tarpn.ax25 import AX25Call
from tarpn.log import LoggingMixin
from tarpn.netrom import NetRomPacket, NetRom
from tarpn.netrom.statemachine import NetRomStateMachine, NetRomStateEvent
from tarpn.network import L3Payload, QoS, L3Protocol
from tarpn.network.netrom_l3 import NetRomAddress
from tarpn.scheduler import Scheduler
from tarpn.settings import NetworkConfig
from tarpn.transport import Protocol, Transport, L4Protocol, L4Address


@dataclass(eq=True, frozen=True)
class NetRomCircuitAddress(L4Address):
    callsign: str
    ssid: int
    circuit: int

    def __str__(self):
        return f"netrom://{self.callsign}-{self.ssid}:{self.circuit}"


class NetRomTransport(Transport):
    def __init__(self, network: NetRom, local_call: AX25Call, remote_call: AX25Call, circuit_id: int,
                 origin_node: AX25Call, origin_user: AX25Call):
        Transport.__init__(self, extra={"peername": (str(remote_call), circuit_id)})
        self.network = network
        self.remote_call = remote_call
        self.local_call = local_call
        self.circuit_id = circuit_id
        self.origin_node = origin_node
        self.origin_user = origin_user
        self.closing = False

    def write(self, data: Any) -> None:
        if isinstance(data, str):
            self.network.nl_data_request(self.circuit_id, self.remote_call, self.local_call, data.encode("utf-8"))
        if isinstance(data, (bytes, bytearray)):
            self.network.nl_data_request(self.circuit_id, self.remote_call, self.local_call, data)

    def close(self) -> None:
        self.network.nl_disconnect_request(self.circuit_id, self.remote_call, self.local_call)
        self.closing = True

    def is_closing(self) -> bool:
        return self.closing

    def get_write_buffer_size(self) -> int:
        return 1000

    def local_address(self) -> Optional[NetRomAddress]:
        return NetRomAddress(self.local_call.callsign,
                             self.local_call.ssid)

    def remote_address(self) -> Optional[NetRomAddress]:
        # TODO
        return None


class NetRomTransportProtocol(NetRom, L4Protocol, LoggingMixin):
    def __init__(self, config: NetworkConfig, l3_protocol: L3Protocol, scheduler: Scheduler):
        self.config = config
        self.sm = NetRomStateMachine(self, scheduler.timer)
        # Mapping of destination addresses to protocol factory. When a new connection is made to the destination
        # we will create a new instance of the protocol as well as a NetRom transport and add it to l3_connections
        self.l3_servers: Dict[AX25Call, Callable[[], Protocol]] = {}

        # This is a mapping of local circuit IDs to (transport, protocol). When incoming data is handled for a
        # circuit, this is how we pass it on to the instance of the protocol
        self.l3_connections: Dict[int, Tuple[Transport, Protocol]] = {}

        # This is a mapping of local circuit ID to a protocol factory. These protocols do not yet have a transport and
        # are in a half-opened state. Once a connect ack is received, a transport will be created and these will be
        # migrated to l3_connections
        self.l3_half_open: Dict[int, Protocol] = {}

        self.l3_destinations: Set[AX25Call] = set()

        self.l3_protocol = l3_protocol
        self.l3_protocol.register_transport_protocol(self)

        def extra():
            return f"[L4]"

        LoggingMixin.__init__(self, logging.getLogger("root"), extra)

    def handle_packet(self, netrom_packet: NetRomPacket) -> bool:
        """
        If this packet is addressed to one of the servers or connections, dispatch it to the state machine
        and then eventually to the transport. Otherwise, drop it.
        """
        if netrom_packet.dest in self.l3_destinations:
            self.sm.handle_packet_sync(netrom_packet)
            return True
        else:
            return False

    def local_call(self) -> AX25Call:
        return AX25Call.parse(self.config.node_call())

    def get_circuit_ids(self) -> List[int]:
        return super().get_circuit_ids()

    def get_circuit(self, circuit_id: int):
        return super().get_circuit(circuit_id)

    def nl_data_request(self, my_circuit_id: int, remote_call: AX25Call, local_call: AX25Call, data: bytes) -> None:
        dest = NetRomAddress(remote_call.callsign, remote_call.ssid)
        routed, mtu = self.l3_protocol.route_packet(dest)
        if routed:
            event = NetRomStateEvent.nl_data(my_circuit_id, remote_call, data, mtu)
            self.sm.handle_internal_event_sync(event)
        else:
            self.debug(f"No route to {dest}, dropping")

    def nl_data_indication(self, my_circuit_idx: int, my_circuit_id: int, remote_call: AX25Call,
                           local_call: AX25Call, data: bytes) -> None:
        self.debug(f"NET/ROM data from {remote_call} on circuit {my_circuit_id}: {data}")
        if my_circuit_idx in self.l3_connections:
            self.l3_connections[my_circuit_idx][1].data_received(data)
        else:
            self.warning(f"Data indication for unknown circuit {my_circuit_idx}")

    def nl_connect_request(self, remote_call: AX25Call, local_call: AX25Call, origin_node: AX25Call,
                           origin_user: AX25Call) -> None:
        nl_connect = NetRomStateEvent.nl_connect(-1, remote_call, local_call, origin_node, origin_user)
        self.sm.handle_internal_event_sync(nl_connect)

    def nl_connect_indication(self, my_circuit_idx: int, my_circuit_id: int, remote_call: AX25Call,
                              local_call: AX25Call, origin_node: AX25Call, origin_user: AX25Call) -> None:
        if my_circuit_idx in self.l3_half_open:
            # Complete a half-opened connection
            self.info(f"Completing half-opened connection to {remote_call} for {origin_node}")
            protocol = self.l3_half_open[my_circuit_idx]
            transport = NetRomTransport(self, local_call, remote_call, my_circuit_idx, origin_node, origin_user)
            protocol.connection_made(transport)
            self.l3_connections[my_circuit_idx] = (transport, protocol)
            del self.l3_half_open[my_circuit_idx]
        elif my_circuit_idx in self.l3_connections:
            # An existing connection, re-connect it
            self.warning(f"Reconnecting to {remote_call} for {origin_node}")
            (transport, protocol) = self.l3_connections[my_circuit_idx]
            protocol.connection_made(transport)
        else:
            # This a new connection, create the transport and protocol
            self.info(f"Creating new connection for server {local_call} to {remote_call}")
            transport = NetRomTransport(self, local_call, remote_call, my_circuit_idx, origin_node, origin_user)
            protocol = self.l3_servers[local_call]()
            protocol.connection_made(transport)
            self.l3_connections[my_circuit_idx] = (transport, protocol)

    def nl_disconnect_request(self, my_circuit_id: int, remote_call: AX25Call, local_call: AX25Call) -> None:
        nl_disconnect = NetRomStateEvent.nl_disconnect(my_circuit_id, remote_call, local_call)
        self.sm.handle_internal_event_sync(nl_disconnect)

    def nl_disconnect_indication(self, my_circuit_idx: int, my_circuit_id: int, remote_call: AX25Call,
                                 local_call: AX25Call) -> None:
        self.info(f"NET/ROM disconnected from {remote_call} on circuit {my_circuit_id}")
        if my_circuit_idx in self.l3_connections:
            self.l3_connections[my_circuit_idx][1].connection_lost(None)
            del self.l3_connections[my_circuit_idx]
        else:
            self.warning(f"Disconnect indication received for unknown circuit {my_circuit_idx}")

    def write_packet(self, packet: NetRomPacket) -> bool:
        source = NetRomAddress(packet.source.callsign, packet.source.ssid, "SOURCE")
        dest = NetRomAddress(packet.dest.callsign, packet.dest.ssid, "DEST")
        payload = L3Payload(source, dest, 0xCF, packet.buffer, -1, QoS.Default, True)
        if self.l3_protocol is not None:
            return self.l3_protocol.send_packet(payload)
        else:
            self.warning("Cannot send, L3 not attached")
            return False

    def open(self, protocol_factory: Callable[[], Protocol], local_call: AX25Call, remote_call: AX25Call,
             origin_node: AX25Call, origin_user: AX25Call) -> Protocol:
        # This a new connection, create the transport and protocol
        nl_connect = NetRomStateEvent.nl_connect(-1, remote_call, local_call, origin_node, origin_user)
        self.sm.handle_internal_event_sync(nl_connect)
        protocol = protocol_factory()
        self.l3_half_open[nl_connect.circuit_id] = protocol
        self.l3_protocol.listen(NetRomAddress(local_call.callsign, local_call.ssid, ""))
        self.l3_destinations.add(local_call)
        return protocol

    def bind_server(self, l3_call: AX25Call, l3_alias: str, protocol_factory: Callable[[], Protocol]):
        """
        Bind a protocol factory to a NetRom destination address
        :param l3_call:             The callsign for the destination
        :param l3_alias:            An alias for this destination (used in NODES broadcast)
        :param protocol_factory:    The protocol factory
        :return:
        """
        self.l3_servers[l3_call] = protocol_factory
        self.l3_protocol.listen(NetRomAddress(l3_call.callsign, l3_call.ssid, l3_alias))
        self.l3_destinations.add(l3_call)
