from dataclasses import dataclass, field
from operator import attrgetter
from typing import List, Dict, Optional, cast

from tarpn.ax25 import AX25Call
from tarpn.netrom import NetRomPacket, NetRomNodes, NodeDestination
from tarpn.network import L3RoutingTable, L3Address

import tarpn.network.netrom_l3 as l3


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
class NetRomRoutingTable(L3RoutingTable):
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

    def route1(self, destination: L3Address) -> Optional[int]:
        if not isinstance(destination, l3.NetRomAddress):
            print(f"Wrong address family, expected NET/ROM got {destination.__class__}")
            return None
        netrom_dest = cast(l3.NetRomAddress, destination)
        packet_dest = AX25Call(netrom_dest.callsign, netrom_dest.ssid)
        # TODO handle alias here
        if packet_dest in self.neighbors:
            return self.neighbors.get(packet_dest).port
        else:
            dest = self.destinations.get(packet_dest)
            if dest:
                neighbors = dest.sorted_neighbors()
                if len(neighbors) > 0:
                    return self.neighbors.get(neighbors[0].neighbor).port
                else:
                    return None
            else:
                return None

    def listen_for_address(self, app_call: AX25Call, app_alias: str):
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
            # Otherwise find best neighbor route
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


