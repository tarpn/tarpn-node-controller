import asyncio
import logging
from asyncio import Lock, AbstractEventLoop, Transport, Protocol
from typing import Dict, List, cast, Any, Callable, Tuple

from tarpn.ax25 import AX25Call, L3Protocol, AX25Packet, UIFrame, AX25StateType
from tarpn.ax25.datalink import DataLinkManager, L3Handler
from tarpn.events import EventBus, EventListener
from tarpn.log import LoggingMixin
from tarpn.netrom import NetRom, NetRomPacket, parse_netrom_packet, parse_netrom_nodes, NetRomNodes
from tarpn.netrom.router import NetRomRoutingTable
from tarpn.netrom.statemachine import NetRomStateMachine, NetRomStateEvent, NetRomStateType
from tarpn.settings import NetworkConfig
from tarpn.util import AsyncioTimer

packet_logger = logging.getLogger("packet")


class NetworkManager(NetRom, L3Handler, LoggingMixin):
    def __init__(self, config: NetworkConfig, loop: AbstractEventLoop):
        self.config = config
        self.sm = NetRomStateMachine(self, AsyncioTimer)
        self.router = NetRomRoutingTable(config.node_alias())
        self.l3_apps: Dict[AX25Call, str] = {}

        # Mapping of destination addresses to protocol factory. When a new connection is made to the destination
        # we will create a new instance of the protocol as well as a NetRom transport and add it to l3_connections
        self.l3_servers: Dict[AX25Call, Callable[[], Protocol]] = {}

        # This is a mapping of local circuit IDs to (transport, protocol). When incoming data is handled for a
        # circuit, this is how we pass it on to the instance of the protocol
        self.l3_connections: Dict[int, Tuple[Transport, Protocol]] = {}

        # This is a mapping of local circuit ID to a protocol factory. These protocols do not yet have a transport and
        # are in a half-opened state. Once a connect ack is received, a transport will be created and these will be
        # migrated to l3_connections
        self.l3_half_open: Dict[int, Callable[[], Protocol]] = {}

        self.data_links: Dict[int, DataLinkManager] = {}
        self.route_lock = Lock()
        self.loop = loop

        def extra():
            return f"[L4 Call={str(config.node_call())} Alias={config.node_alias()}]"
        LoggingMixin.__init__(self, logging.getLogger("main"), extra)

    async def start(self):
        self.info("Starting NetworkManager")
        asyncio.create_task(self._broadcast_nodes_loop())
        await self.sm.start()

    def can_handle(self, protocol: L3Protocol) -> bool:
        """L3Handler.can_handle"""
        return protocol == L3Protocol.NetRom

    def maybe_handle_special(self, port: int, packet: AX25Packet) -> bool:
        """L3Handler.maybe_handle_special"""
        if type(packet) == UIFrame:
            ui = cast(UIFrame, packet)
            if ui.protocol == L3Protocol.NetRom and ui.dest == AX25Call("NODES"):
                # Parse this NODES packet and mark it as handled
                nodes = parse_netrom_nodes(ui.info)
                EventBus.emit("netrom.nodes", [nodes])
                asyncio.get_event_loop().create_task(self._update_nodes(packet.source, port, nodes))
                # Stop further processing
                return False
        return True

    def handle(self, port: int, remote_call: AX25Call, data: bytes):
        """L3Handler.handle"""
        try:
            netrom_packet = parse_netrom_packet(data)
            asyncio.create_task(self._handle_packet_async(netrom_packet))
            return True
        except:
            return False

    def local_call(self) -> AX25Call:
        return AX25Call.parse(self.config.node_call())

    async def _handle_packet_async(self, netrom_packet: NetRomPacket):
        """If packet is for us, handle it, otherwise forward it using our L3 routing table"""

        EventBus.emit("netrom.incoming", [netrom_packet])
        self.debug(f"RX: {netrom_packet}")
        packet_logger.info(f"RX: {netrom_packet}")

        if netrom_packet.dest == AX25Call("KEEPLI-0"):
            # What are these?? Just ignore them
            pass
        elif netrom_packet.dest == AX25Call.parse(self.config.node_call()):
            # Destination is this node
            self.sm.handle_packet(netrom_packet)
        elif netrom_packet.dest in self.l3_apps:
            # Destination is an app served by this node
            self.sm.handle_packet(netrom_packet)
        elif netrom_packet.dest in self.l3_servers:
            # Destination is an app served by this node
            self.sm.handle_packet(netrom_packet)
        else:
            # Destination is somewhere else
            self.write_packet(netrom_packet, forward=True)

    def get_circuit_ids(self) -> List[int]:
        return self.sm.get_circuits()

    def get_circuit(self, circuit_id: int):
        return self.sm._get_circuit(circuit_id, circuit_id)  # TODO fix this

    def nl_data_request(self, my_circuit_id: int, remote_call: AX25Call, local_call: AX25Call, data: bytes):
        event = NetRomStateEvent.nl_data(my_circuit_id, remote_call, data)
        event.future = self.loop.create_future()
        self.sm.handle_internal_event(event)
        return event.future

    def nl_data_indication(self, my_circuit_idx: int, my_circuit_id: int,
                           remote_call: AX25Call, local_call: AX25Call, data: bytes):
        # Called from the state machine to indicate data to higher layers
        EventBus.emit(f"netrom.{local_call}.inbound", my_circuit_idx, remote_call, data)
        if my_circuit_idx in self.l3_connections:
            self.l3_connections[my_circuit_idx][1].data_received(data)
        else:
            self.warning(f"Data indication for unknown circuit {my_circuit_idx}")

    def nl_connect_request(self, remote_call: AX25Call, local_call: AX25Call,
                           origin_node: AX25Call, origin_user: AX25Call):
        if remote_call == local_call:
            raise RuntimeError(f"Cannot connect to node's own callsign {local_call}")

        # circuit_id of -1 means pick an unused circuit to use
        nl_connect = NetRomStateEvent.nl_connect(-1, remote_call, local_call, origin_node, origin_user)
        self.sm.handle_internal_event(nl_connect)

        async def poll():
            while True:
                if self.sm.get_state(nl_connect.circuit_id) == NetRomStateType.Connected:
                    return nl_connect.circuit_id
                else:
                    await asyncio.sleep(0.001)
        return asyncio.create_task(poll())

    def nl_connect_indication(self, my_circuit_idx: int, my_circuit_id: int,
                              remote_call: AX25Call, local_call: AX25Call,
                              origin_node: AX25Call, origin_user: AX25Call):
        # Send a connect event
        EventBus.emit(f"netrom.{local_call}.connect", my_circuit_idx, remote_call)

        if my_circuit_idx in self.l3_half_open:
            # Complete a half-opened connection
            protocol_factory = self.l3_half_open[my_circuit_idx]
            protocol = protocol_factory()
            transport = NetworkTransport(self, local_call, remote_call, my_circuit_idx, origin_node, origin_user)
            protocol.connection_made(transport)
            self.l3_connections[my_circuit_idx] = (transport, protocol)
            del self.l3_half_open[my_circuit_idx]
        elif local_call in self.l3_servers:
            if my_circuit_idx in self.l3_connections:
                # An existing connection, re-connect it
                self.l3_connections[my_circuit_idx][1].connection_lost(RuntimeError("Remote end reconnected"))
                self.l3_connections[my_circuit_idx][1].connection_made(self.l3_connections[my_circuit_idx][0])
            else:
                # This a new connection, create the transport and protocol
                transport = NetworkTransport(self, local_call, remote_call, my_circuit_idx, origin_node, origin_user)
                protocol = self.l3_servers[local_call]()
                protocol.connection_made(transport)
                self.l3_connections[my_circuit_idx] = (transport, protocol)
        else:
            self.warning(f"Got unexpected connection from {remote_call} at {local_call}:{my_circuit_idx}")

    def nl_disconnect_request(self, my_circuit_id: int, remote_call: AX25Call, local_call: AX25Call):
        nl_disconnect = NetRomStateEvent.nl_disconnect(my_circuit_id, remote_call, local_call)
        self.sm.handle_internal_event(nl_disconnect)

        async def poll():
            while True:
                if self.sm.get_state(nl_disconnect.circuit_id) == NetRomStateType.Disconnected:
                    return nl_disconnect.circuit_id
                else:
                    await asyncio.sleep(0.001)
        return asyncio.create_task(poll())

    def nl_disconnect_indication(self, my_circuit_idx: int, my_circuit_id: int,
                                 remote_call: AX25Call, local_call: AX25Call):
        EventBus.emit(f"netrom.{local_call}.disconnect", my_circuit_idx, remote_call)

        if local_call in self.l3_servers:
            if my_circuit_idx in self.l3_connections:
                self.l3_connections[my_circuit_idx][1].connection_lost(None)
                del self.l3_connections[my_circuit_idx]
            else:
                self.warning(f"Disconnect indication received for unknown circuit {my_circuit_idx}")

    # TODO error indication

    def write_packet(self, packet: NetRomPacket, forward: bool = False) -> bool:
        possible_routes = self.router.route(packet)
        routed = False
        for route in possible_routes:
            neighbor = self.router.neighbors.get(str(route))
            if neighbor is None:
                self.logger.warning(f"No neighbor for route {route}")
                continue
            #print(f"Trying route {route} to neighbor {neighbor}")
            data_link = self.data_links.get(neighbor.port)

            if data_link.link_state(neighbor.call) == AX25StateType.Connected:
                data_link.dl_data_request(neighbor.call, L3Protocol.NetRom, packet.buffer)
                routed = True
                EventBus.emit("netrom.outbound", [packet])
                if forward:
                    # Log this transmission differently if it's being forwarded
                    self.logger.info(f"[L3 Route={route} Neighbor={neighbor.call}] TX: {packet}")
                else:
                    self.debug(f"TX: {packet}")
                    packet_logger.info(f"TX: {packet}")
                break

        if not routed:
            self.warning(f"Could not route packet to {packet.dest}. Possible routes were {possible_routes}")
            pass

        return routed

    def attach_data_link(self, data_link: DataLinkManager):
        self.data_links[data_link.link_port] = data_link
        self.router.our_calls.add(data_link.link_call)
        data_link.add_l3_handler(self)

    def bind(self, l4_call: AX25Call, l4_alias: str):
        self.router.listen_for_address(l4_call, l4_alias)
        self.l3_apps[l4_call] = l4_alias

        # Need to bind this here so the application can start sending packets right away
        EventBus.bind(EventListener(
            f"netrom.{l4_call}.outbound",
            f"netrom_{l4_call}_outbound",
            lambda remote_call, data: self.nl_data_request(remote_call, l4_call, data)
        ), True)

    def bind_server(self, l3_call: AX25Call, l3_alias: str, protocol_factory: Callable[[], Protocol]):
        """
        Bind a protocol factory to a NetRom destination address
        :param l3_call:             The callsign for the destination
        :param l3_alias:            An alias for this destination (used in NODES broadcast)
        :param protocol_factory:    The protocol factory
        :return:
        """
        self.router.listen_for_address(l3_call, l3_alias)
        self.l3_servers[l3_call] = protocol_factory

    def open(self, protocol_factory: Callable[[], Protocol], local_call: AX25Call, remote_call: AX25Call,
             origin_node: AX25Call, origin_user: AX25Call):
        # This a new connection, create the transport and protocol
        nl_connect = NetRomStateEvent.nl_connect(-1, remote_call, local_call, origin_node, origin_user)
        self.sm.handle_internal_event(nl_connect)
        self.l3_half_open[nl_connect.circuit_id] = protocol_factory


    async def _maybe_open_data_link(self, port: int, remote_call: AX25Call):
        data_link = self.data_links.get(port)
        if data_link.link_state(remote_call) in (AX25StateType.Disconnected, AX25StateType.AwaitingRelease):
            # print(f"Opening data link to {remote_call}")
            done, pending = await asyncio.wait({data_link.dl_connect_request(remote_call)}, timeout=5.000)
            if len(pending) != 0:
                self.warning(f"Timed out waiting on data link with {remote_call}")

    async def _update_nodes(self, heard_from: AX25Call, heard_on: int, nodes: NetRomNodes):
        async with self.route_lock:
            #print(f"Got Nodes\n{nodes}")
            self.router.update_routes(heard_from, heard_on, nodes)
            #self.info(f"New Routing Table: {self.router}")
            asyncio.create_task(self._maybe_open_data_link(heard_on, heard_from))
            #print(f"New routing table\n{self.router}")
        await asyncio.sleep(10)

    async def _broadcast_nodes(self):
        nodes = self.router.get_nodes()
        nodes.save("nodes.json")
        for dl in self.data_links.values():
            for nodes_packet in nodes.to_packets(AX25Call.parse(self.config.node_call())):
                dl.write_packet(nodes_packet)

    async def _broadcast_nodes_loop(self):
        await asyncio.sleep(10)  # initial delay
        while True:
            async with self.route_lock:
                self.router.prune_routes()
            await self._broadcast_nodes()
            await asyncio.sleep(self.config.nodes_interval())


class NetworkTransport(Transport):
    def __init__(self, network: NetRom, local_call: AX25Call, remote_call: AX25Call, circuit_id: int,
                 origin_node: AX25Call, origin_user: AX25Call):
        Transport.__init__(self, extra={"peername": (str(remote_call), circuit_id)})
        self.network = network
        self.remote_call = remote_call
        self.local_call = local_call
        self.circuit_id = circuit_id
        self.origin_node = origin_node
        self.origin_user = origin_user

    def write(self, data: Any) -> None:
        self.network.nl_data_request(self.circuit_id, self.remote_call, self.local_call, data)

    def close(self) -> None:
        self.network.nl_disconnect_request(self.circuit_id, self.remote_call, self.local_call)
