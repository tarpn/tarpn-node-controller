import datetime
import logging
import threading
from dataclasses import dataclass, field
from typing import cast, Optional, Tuple

from tarpn.ax25 import AX25Call
from tarpn.datalink import L2Payload
from tarpn.datalink.ax25_l2 import AX25Address, LinkMultiplexer, AX25Protocol
from tarpn.datalink.protocol import L2Protocol
from tarpn.log import LoggingMixin
from tarpn.metrics import MetricsMixin
from tarpn.network import L3Protocol, L3Payload, QoS, L3Address
from tarpn.scheduler import Scheduler
from tarpn.transport import L4Protocol
from tarpn.netrom import parse_netrom_packet, parse_netrom_nodes, NetRomNodes


packet_logger = logging.getLogger("packet")


@dataclass
class NetRomAddress(L3Address):
    callsign: str = field(compare=True)
    ssid: int = field(compare=True)
    node_alias: Optional[str] = field(compare=False, default=None)

    def as_call(self):
        return AX25Call(self.callsign, self.ssid)

    @classmethod
    def from_call(cls, ax25_call: AX25Call):
        return cls(callsign=ax25_call.callsign, ssid=ax25_call.ssid)


class NetRomL3(L3Protocol, LoggingMixin, MetricsMixin):
    def __init__(self,
                 node_call: AX25Call,
                 node_alias: str,
                 scheduler: Scheduler,
                 link_multiplexer: LinkMultiplexer,
                 routing_table):
        LoggingMixin.__init__(self)
        self.node_call = node_call
        self.node_alias = node_alias
        self.link_multiplexer = link_multiplexer
        self.router = routing_table
        self.nodes_timer = scheduler.timer(60000.0, self.broadcast_nodes)
        self.netrom_transport = None
        self.nodes_lock = threading.Lock()

        # Register our-calls with routing table
        for l2 in link_multiplexer.l2_devices.values():
            l2_address = l2.get_link_address()
            if isinstance(l2_address, AX25Address):
                self.router.our_calls.add(cast(AX25Address, l2_address).to_ax25_call())

        now = datetime.datetime.now()
        prune_intervals = round((now - self.router.updated_at).seconds / 60.000)
        if prune_intervals < 10:
            self.info(f"Pruning routes {prune_intervals} times")
            for i in range(prune_intervals):
                self.router.prune_routes()
        else:
            self.info("Routes too old, discarding.")
            self.router.clear_routes()

        self.nodes_timer.start()
        self.broadcast_nodes()

    def register_transport_protocol(self, l4_protocol: L4Protocol):
        # Only support NET/ROM
        #if isinstance(l4_protocol, tarpn.transport.netrom_l4.NetRomTransport):
        self.netrom_transport = l4_protocol
        #else:
        #    raise Exception("Only NET/ROM transport supported")

    def can_handle(self, protocol: int) -> bool:
        return protocol == 0xCF

    def handle_l2_payload(self, payload: L2Payload):
        if payload.destination == AX25Address("NODES"):
            nodes = parse_netrom_nodes(payload.l3_data)
            packet_logger.debug(f"RX: {nodes}")
            if isinstance(payload.source, AX25Address):
                heard_from = cast(AX25Address, payload.source)
                found_self = False
                for dest in nodes.destinations:
                    if dest.dest_node == self.node_call:
                        found_self = True
                        break
                self.router.update_routes(heard_from.to_ax25_call(), payload.link_id, nodes)
                self.router.prune_routes()
                self.router.save(f"nodes-{self.node_call}.json")

                if not found_self:
                    heard_from_l2 = self.link_multiplexer.get_link(payload.link_id)
                    with self.nodes_lock:
                        self.send_nodes_to_link(self.router.get_nodes(), heard_from_l2)
            else:
                self.error("NET/ROM only supports AX.25 L2")
        else:
            netrom_packet = parse_netrom_packet(payload.l3_data)

            if isinstance(payload.source, AX25Address):
                heard_from = cast(AX25Address, payload.source)
                self.router.refresh_route(str(heard_from.to_ax25_call()), str(netrom_packet.source))

            packet_logger.info(f"RX: {netrom_packet}")
            if not self.netrom_transport.handle_packet(netrom_packet):
                # If not handled by upper layers, try to route this packet
                source_address = NetRomAddress(netrom_packet.source.callsign, netrom_packet.source.ssid)
                dest_address = NetRomAddress(netrom_packet.dest.callsign, netrom_packet.dest.ssid)
                link_to_route = self.router.route1(dest_address)
                if link_to_route is not None:
                    l3_payload = L3Payload(source_address, dest_address,
                                           0xCF, netrom_packet.buffer, link_to_route, QoS.Default, True)
                    if self.link_multiplexer.get_queue(link_to_route).offer(l3_payload):
                        packet_logger.info(f"FWD: {netrom_packet}")
                    else:
                        packet_logger.info(f"DROP: {netrom_packet}")
                else:
                    # No route, drop it
                    self.warning(f"Net/Rom dropping: {netrom_packet}, no route to destination")
                    packet_logger.info(f"DROP: {netrom_packet}")

    def route_packet(self, address: L3Address) -> Tuple[bool, int]:
        link_to_route = self.router.route1(address)
        if link_to_route is not None:
            l2_mtu = self.link_multiplexer.get_link(link_to_route).maximum_transmission_unit()
            return True, l2_mtu - 20  # NET/ROM network headers are 20 bytes
        else:
            return False, 0

    def send_packet(self, payload: L3Payload) -> bool:
        link_to_route = self.router.route1(payload.destination)
        if link_to_route is not None:
            payload.link_id = link_to_route
            if self.link_multiplexer.get_queue(payload.link_id).offer(payload):
                packet_logger.info(f"TX: {payload}")
                return True
            else:
                self.warning(f"Could not enqueue payload {payload}, dropping")
                packet_logger.debug(f"DROP: {payload}")
                return False
        else:
            self.warning(f"Could not route payload {payload}, dropping")
            packet_logger.debug(f"DROP: {payload}")
            return False

    def listen(self, address: NetRomAddress):
        self.router.listen_for_address(address.as_call(), address.node_alias)

    def broadcast_nodes(self):
        with self.nodes_lock:
            self.counter("broadcast").inc()
            self.router.prune_routes()
            nodes = self.router.get_nodes()
            for l2 in self.link_multiplexer.l2_devices.values():
                self.send_nodes_to_link(nodes, l2)
            # Reset the timer
            self.nodes_timer.reset()

    def send_nodes_to_link(self, nodes: NetRomNodes, l2: L2Protocol):
        # not thread safe
        if isinstance(l2, AX25Protocol):
            # Special NET/ROM + AX.25 behavior
            for chunk in nodes.to_chunks():
                link_id = l2.maybe_create_logical_link(AX25Call("NODES"))
                payload = L3Payload(L3Address(), L3Address(), 0xCF, chunk, link_id, QoS.Lowest, reliable=False)
                packet_logger.debug(f"TX: {payload}")
                self.link_multiplexer.get_queue(link_id).offer(payload)

