import asyncio
import datetime
import json
import logging
import os
from asyncio import Lock, AbstractEventLoop
from dataclasses import dataclass, field
from itertools import islice
from operator import attrgetter
from typing import Dict, List, cast, Iterator

from tarpn.ax25 import AX25Call, L3Protocol, AX25Packet, UIFrame, parse_ax25_call, SupervisoryCommand, AX25StateType
from tarpn.ax25.datalink import DataLinkManager, L3Handler
from tarpn.events import EventBus, EventListener
from tarpn.logging import LoggingMixin
from tarpn.netrom import NetRom, NetRomPacket, parse_netrom_packet
from tarpn.netrom.statemachine import NetRomStateMachine, NetRomStateEvent, NetRomStateType
from tarpn.settings import NetworkConfig
from tarpn.util import chunks


@dataclass
class Neighbor:
    call: AX25Call
    port: int
    quality: int


@dataclass
class Route:
    neighbor: AX25Call
    dest: AX25Call
    next_hop: AX25Call
    quality: int
    obsolescence: int


@dataclass
class Destination:
    node_call: AX25Call
    node_alias: str
    neighbor_map: Dict[AX25Call, Route] = field(default_factory=dict)
    freeze: bool = False

    def sorted_neighbors(self):
        return sorted(self.neighbor_map.values(), key=attrgetter("quality"), reverse=True)


@dataclass
class NodeDestination:
    dest_node: AX25Call
    dest_alias: str
    best_neighbor: AX25Call
    quality: int

    def __post_init__(self):
        self.quality = int(self.quality)


@dataclass
class NetRomNodes:
    sending_alias: str
    destinations: List[NodeDestination]

    def to_packets(self, source: AX25Call) -> Iterator[UIFrame]:
        for dest_chunk in chunks(self.destinations, 11):
            nodes_chunk = NetRomNodes(self.sending_alias, dest_chunk)
            yield UIFrame.ui_frame(AX25Call("NODES", 0), source, [], SupervisoryCommand.Command,
                                   False, L3Protocol.NetRom, encode_netrom_nodes(nodes_chunk))

    def save(self, source: AX25Call, file: str):
        nodes_json = {
            "nodeCall": str(source),
            "nodeAlias": self.sending_alias,
            "createdAt": datetime.datetime.now().isoformat(),
            "destinations": [{
                "nodeCall": str(d.dest_node),
                "nodeAlias": d.dest_alias,
                "bestNeighbor": str(d.best_neighbor),
                "quality": d.quality
            } for d in self.destinations]
        }
        with open(file, "w") as fp:
            json.dump(nodes_json, fp, indent=2)

    @classmethod
    def load(cls, source: AX25Call, file: str):
        if not os.path.exists(file):
            return
        with open(file) as fp:
            nodes_json = json.load(fp)
            assert str(source) == nodes_json["nodeCall"]
            sending_alias = nodes_json["nodeAlias"]
            destinations = []
            for dest_json in nodes_json["destinations"]:
                destinations.append(NodeDestination(
                    AX25Call.parse(dest_json["nodeCall"]),
                    dest_json["nodeAlias"],
                    AX25Call.parse(dest_json["bestNeighbor"]),
                    int(dest_json["quality"])
                ))
            return cls(sending_alias, destinations)


@dataclass
class RoutingTable:
    node_alias: str
    our_calls: List[AX25Call] = field(default_factory=list)

    # Neighbors is a map of direct neighbors we have, i.e., who we have heard NODES from
    neighbors: Dict[AX25Call, Neighbor] = field(default_factory=dict)

    # Destinations is the content of the NODES table, what routes exist to other nodes through which neighbors
    destinations: Dict[AX25Call, Destination] = field(default_factory=dict)

    # TODO config all these
    default_obs: int = 100
    default_quality: int = 255
    min_quality: int = 50
    min_obs: int = 4

    def __repr__(self):
        s = "Neighbors:\n"
        for neighbor in self.neighbors.values():
            s += f"\t{neighbor}\n"
        s += "Destinations:\n"
        for dest in self.destinations.values():
            s += f"\t{dest}\n"
        return s.strip()

    def route(self, packet: NetRomPacket) -> List[AX25Call]:
        """
        If a packet's destination is a known neighbor, route to it. Otherwise look up the route with the highest
        quality and send the packet to the neighbor which provided that route
        :param packet:
        :return: list of neighbor callsign's in sorted order of route quality
        """
        if packet.dest in self.neighbors:
            return [packet.dest]
        else:
            dest = self.destinations.get(packet.dest)
            if dest:
                return [n.neighbor for n in dest.sorted_neighbors()]
            else:
                return []

    def bind_application(self, app_call: AX25Call, app_alias: str):
        app_routes = {}
        for our_call in self.our_calls:
            app_routes[our_call] = Route(our_call, app_call, our_call, 95, 100)
        self.destinations[app_call] = Destination(app_call, app_alias, app_routes, True)

    def update_routes(self, heard_from: AX25Call, heard_on_port: int, nodes: NetRomNodes):
        """
        Update the routing table with a NODES broadcast.

        This method is not thread-safe.
        """
        # Get or create the neighbor and destination
        neighbor = self.neighbors.get(heard_from, Neighbor(heard_from, heard_on_port, self.default_quality))
        self.neighbors[heard_from] = neighbor

        # Add direct route to whoever sent the NODES
        dest = self.destinations.get(heard_from, Destination(heard_from, nodes.sending_alias))
        dest.neighbor_map[heard_from] = Route(heard_from, heard_from, heard_from,
                                              self.default_quality, self.default_obs)
        self.destinations[heard_from] = dest

        for destination in nodes.destinations:
            # Filter out ourselves
            route_quality = 0
            if destination.best_neighbor in self.our_calls:
                # Best neighbor is us, this is a "trivial loop", quality is zero
                continue
            else:
                # Otherwise compute this route's quality based on the NET/ROM spec
                route_quality = (destination.quality * neighbor.quality + 128.) / 256.

            # Only add routes which are above the minimum quality to begin with TODO check this logic
            if route_quality > self.min_quality:
                new_dest = self.destinations.get(
                    destination.dest_node, Destination(destination.dest_node, destination.dest_alias))
                new_route = new_dest.neighbor_map.get(
                    neighbor.call, Route(neighbor.call, destination.dest_node, destination.best_neighbor,
                                         int(route_quality), self.default_obs))
                new_route.quality = route_quality
                new_route.obsolescence = self.default_obs
                new_dest.neighbor_map[neighbor.call] = new_route
                self.destinations[destination.dest_node] = new_dest
            else:
                # print(f"Saw new route for {destination}, but quality was too low")
                pass

    def prune_routes(self) -> None:
        """
        Prune any routes which we haven't heard about in a while.

        This method is not thread-safe.
        """
        # print("Pruning routes")
        for call, destination in list(self.destinations.items()):
            if destination.freeze:
                # Don't prune frozen routes
                continue
            for neighbor, route in list(destination.neighbor_map.items()):
                route.obsolescence -= 1
                if route.obsolescence <= 0:
                    # print(f"Removing {neighbor} from {destination} neighbor's list")
                    del destination.neighbor_map[neighbor]
            if len(destination.neighbor_map.keys()) == 0:
                # print(f"No more routes to {call}, removing from routing table")
                del self.destinations[call]
                if call in self.neighbors:
                    del self.neighbors[call]

    def get_nodes(self) -> NetRomNodes:
        node_destinations = []
        for destination in self.destinations.values():
            best_neighbor = None
            for neighbor in destination.sorted_neighbors():
                if neighbor.obsolescence >= self.min_obs:
                    best_neighbor = neighbor
                    break
                else:
                    # print(f"Not including {neighbor} in NODES, obsolescence below threshold")
                    pass
            if best_neighbor:
                node_destinations.append(NodeDestination(destination.node_call, destination.node_alias,
                                                         best_neighbor.next_hop, best_neighbor.quality))
            else:
                #  print(f"No good neighbor was found for {destination}")
                pass
        return NetRomNodes(self.node_alias, node_destinations)


def parse_netrom_nodes(data: bytes) -> NetRomNodes:
    bytes_iter = iter(data)
    assert next(bytes_iter) == 0xff
    sending_alias = bytes(islice(bytes_iter, 6)).decode("ASCII", "replace").strip()
    destinations = []
    while True:
        try:
            dest = parse_ax25_call(bytes_iter)
            alias = bytes(islice(bytes_iter, 6)).decode("ASCII", "replace").strip()
            neighbor = parse_ax25_call(bytes_iter)
            quality = next(bytes_iter)
            destinations.append(NodeDestination(dest, alias, neighbor, quality))
        except StopIteration:
            break
    return NetRomNodes(sending_alias, destinations)


def encode_netrom_nodes(nodes: NetRomNodes) -> bytes:
    b = bytearray()
    b.append(0xff)
    b.extend(nodes.sending_alias.ljust(6, " ").encode("ASCII"))
    for dest in nodes.destinations:
        dest.dest_node.write(b)
        b.extend(dest.dest_alias.ljust(6, " ").encode("ASCII"))
        dest.best_neighbor.write(b)
        b.append(dest.quality & 0xff)
    return bytes(b)

class NetworkManager(NetRom, L3Handler, LoggingMixin):
    def __init__(self, config: NetworkConfig, loop: AbstractEventLoop):
        self.config = config
        self.sm = NetRomStateMachine(self)
        self.router = RoutingTable(config.node_alias())
        self.l3_apps: Dict[AX25Call, str] = {}
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
        self.info(f"RX: {netrom_packet}")

        if netrom_packet.dest == AX25Call("KEEPLI-0"):
            # What are these?? Just ignore them
            pass
        elif netrom_packet.dest == AX25Call.parse(self.config.node_call()):
            # Destination is this node
            self.sm.handle_packet(netrom_packet)
        elif netrom_packet.dest in self.l3_apps:
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

    def nl_connect_request(self, remote_call: AX25Call, local_call: AX25Call):
        if remote_call == local_call:
            raise RuntimeError(f"Cannot connect to node's own callsign {local_call}")

        # circuit_id of -1 means pick an unused circuit to use
        nl_connect = NetRomStateEvent.nl_connect(-1, remote_call, local_call)
        self.sm.handle_internal_event(nl_connect)

        async def poll():
            while True:
                if self.sm.get_state(nl_connect.circuit_id) == NetRomStateType.Connected:
                    return nl_connect.circuit_id
                else:
                    await asyncio.sleep(0.001)
        return asyncio.create_task(poll())

    def nl_connect_indication(self, my_circuit_idx: int, my_circuit_id: int,
                              remote_call: AX25Call, local_call: AX25Call):
        # Send a connect event
        EventBus.emit(f"netrom.{local_call}.connect", my_circuit_idx, remote_call)

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

    # TODO error indication

    def write_packet(self, packet: NetRomPacket, forward: bool = False) -> bool:
        possible_routes = self.router.route(packet)
        routed = False
        for route in possible_routes:
            neighbor = self.router.neighbors.get(route)
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
                    self.info(f"TX: {packet}")
                break

        if not routed:
            self.warning(f"Could not route packet to {packet.dest}. Possible routes were {possible_routes}")
            pass

        return routed

    def attach_data_link(self, data_link: DataLinkManager):
        self.data_links[data_link.link_port] = data_link
        self.router.our_calls.append(data_link.link_call)
        data_link.add_l3_handler(self)

    def bind(self, l4_call: AX25Call, l4_alias: str):
        self.router.bind_application(l4_call, l4_alias)
        self.l3_apps[l4_call] = l4_alias

        # Need to bind this here so the application can start sending packets right away
        EventBus.bind(EventListener(
            f"netrom.{l4_call}.outbound",
            f"netrom_{l4_call}_outbound",
            lambda remote_call, data: self.nl_data_request(remote_call, l4_call, data)
        ), True)

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
        nodes.save(AX25Call.parse(self.config.node_call()), "nodes.json")
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
