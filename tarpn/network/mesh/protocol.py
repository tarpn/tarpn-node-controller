import dataclasses
import enum
import queue
import threading
import time
import traceback
from collections import defaultdict
from datetime import datetime, timedelta
from functools import partial
from io import BytesIO
from typing import Tuple, List, Dict, Optional, Set, cast

import networkx as nx
from networkx import NetworkXNoPath, NetworkXException

from tarpn.datalink import L2Payload
from tarpn.datalink.ax25_l2 import AX25Address
from tarpn.datalink.protocol import LinkMultiplexer
from tarpn.log import LoggingMixin
from tarpn.network import L3Protocol, L3Address, L3Payload, QoS
from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.header import Protocol, NetworkHeader, Header, HelloHeader, LinkStateHeader, \
    LinkStateAdvertisementHeader, LinkStateQueryHeader, ControlHeader, ControlType, RecordHeader, RecordType
from tarpn.network.mesh.ping import PingProtocol
from tarpn.scheduler import Scheduler, CloseableThreadLoop
from tarpn.settings import NetworkConfig
from tarpn.transport.mesh import L4Handlers
from tarpn.util import Time, TTLCache, lollipop_sequence, lollipop_compare


def encode_packet(network_header: NetworkHeader, additional_headers: List[Header], payload: bytes) -> bytes:
    stream = BytesIO()
    network_header.encode(stream)
    for header in additional_headers:
        header.encode(stream)
    stream.write(payload)
    stream.seek(0)
    return stream.read()


def encode_partial_packet(headers: List[Header], payload: bytes) -> bytes:
    stream = BytesIO()
    for header in headers:
        header.encode(stream)
    stream.write(payload)
    stream.seek(0)
    return stream.read()


class NeighborState(enum.Enum):
    DOWN = 1    # We know nothing
    INIT = 2    # We've seen HELLO
    UP = 3      # We've seen ourselves in a HELLO


@dataclasses.dataclass
class Neighbor:
    address: MeshAddress
    name: str
    link_id: int
    neighbors: List[MeshAddress]
    last_update: datetime
    state: NeighborState


@dataclasses.dataclass
class LinkState:
    local: MeshAddress
    remote: MeshAddress
    quality: int
    last_update: datetime


class MeshProtocol(CloseableThreadLoop, L3Protocol, LoggingMixin):
    """
    A simple protocol for a partially connected mesh network.

    Nodes send HELLO packets to their neighbors frequently. This is used as a way to initialize
    a link to a neighbor and as a failure detector.

    Once a neighbor is discovered, a ADVERTISE packet is sent which informs the neighbor of this node's
    current state. If a node receives an ADVERTISE with a newer epoch, it will forward that packet so
    other nodes can learn about this new state.
    """

    ProtocolId = 0xB0
    WindowSize = 1024
    MaxFragments = 8
    HeaderBytes = 10
    DefaultTTL = 7
    BroadcastAddress = MeshAddress(0xFFFF)

    def __init__(self,
                 time: Time,
                 config: NetworkConfig,
                 link_multiplexer: LinkMultiplexer,
                 l4_handlers: L4Handlers,
                 scheduler: Scheduler):
        LoggingMixin.__init__(self, extra_func=self.log_ident)
        CloseableThreadLoop.__init__(self, name="MeshNetwork")

        self.time = time
        self.config = config
        self.link_multiplexer = link_multiplexer
        self.l4_handlers = l4_handlers
        self.scheduler = scheduler

        self.queue = queue.Queue()
        self.our_address = MeshAddress.parse(config.get("mesh.address"))
        self.host_name = config.get("host.name")
        self.ping_protocol = PingProtocol(self)

        # TTL cache of seen frames from each source
        self.header_cache: TTLCache = TTLCache(time, 30_000)

        # Our own send sequence
        self.send_seq: int = 1
        self.seq_lock = threading.Lock()

        # Link states and neighbors
        self.neighbors: Dict[MeshAddress, Neighbor] = dict()

        # An epoch for our own link state changes. Any time a neighbor comes or goes, or the quality changes,
        # we increment this counter. Uses a "lollipop sequence" to allow for easy detection of wrap around vs
        # reset
        self.our_link_state_epoch_generator = lollipop_sequence()
        self.our_link_state_epoch: int = next(self.our_link_state_epoch_generator)

        # Epochs we have received from other nodes and their link states
        self.link_state_epochs: Dict[MeshAddress, int] = dict()
        self.host_names: Dict[MeshAddress, str] = dict()
        self.link_states: Dict[MeshAddress, List[LinkStateHeader]] = dict()

        self.last_hello_time = datetime.fromtimestamp(0)
        self.last_advert = datetime.utcnow()
        self.last_query = datetime.utcnow()
        self.last_epoch_bump = datetime.utcnow()

        self.scheduler.timer(1_000, partial(self.scheduler.submit, self), True)

    def __repr__(self):
        return f"<MeshProtocol {self.our_address}>"

    def log_ident(self) -> str:
        return f"[MeshProtocol {self.our_address}]"

    def next_sequence(self) -> int:
        with self.seq_lock:
            seq = self.send_seq
            self.send_seq += 1
        return seq % MeshProtocol.WindowSize

    def next_sequences(self, n) -> List[int]:
        seqs = []
        with self.seq_lock:
            for i in range(n):
                seqs.append(self.send_seq % MeshProtocol.WindowSize)
            self.send_seq += n
        return seqs

    def neighbors(self, since=300) -> Set[MeshAddress]:
        return set(self.neighbors.keys())

    def up_neighbors(self) -> List[MeshAddress]:
        return [n.address for n in self.neighbors.values() if n.state == NeighborState.UP]

    def alive_neighbors(self) -> List[MeshAddress]:
        return [n.address for n in self.neighbors.values() if n.state in (NeighborState.UP, NeighborState.INIT)]

    def down_neighbors(self) -> List[MeshAddress]:
        return [n.address for n in self.neighbors.values() if n.state == NeighborState.DOWN]

    def valid_link_states(self) -> Dict[MeshAddress, List[LinkStateHeader]]:
        now = datetime.utcnow()
        result = dict()
        for node, link_states in self.link_states.items():
            result[node] = []
            for link_state in link_states:
                if (now - link_state.created).seconds < 300:
                    result[node].append(link_state)
        return result

    def can_handle(self, protocol: int) -> bool:
        return protocol == MeshProtocol.ProtocolId

    def pre_close(self):
        # Erase our neighbors and send ADVERT
        self.neighbors.clear()
        self.our_link_state_epoch = next(self.our_link_state_epoch_generator)
        self.send_advertisement()
        time.sleep(1)  # TODO better solution is to wait for L3 queue to drain in close

    def close(self):
        self.wakeup()
        CloseableThreadLoop.close(self)

    def iter_loop(self):
        # Check if we need to take some periodic action like sending a HELLO
        now = datetime.utcnow()  # TODO use time.time_ns instead of datetime
        deadline = min([
            self.check_dead_neighbors(now),
            self.check_hello(now),
            self.check_epoch(now),
            self.check_advert(now),
            self.check_query(now)
        ])

        # Now wait at most the deadline for the next action for new incoming packets
        try:
            event = self.queue.get(block=True, timeout=deadline)
            if event is not None:
                self.process_incoming(event)
        except queue.Empty:
            return

    def wakeup(self):
        """Wake up the main thread"""
        self.queue.put(None)

    def check_dead_neighbors(self, now: datetime) -> int:
        min_deadline = self.config.get_int("mesh.dead.interval")
        for neighbor in list(self.neighbors.values()):
            if neighbor.state == NeighborState.DOWN:
                continue

            deadline = self.config.get_int("mesh.dead.interval") - (now - neighbor.last_update).seconds
            if deadline <= 0:
                self.info(f"Neighbor {neighbor.address} detected as DOWN!")

                neighbor.state = NeighborState.DOWN
                self.our_link_state_epoch = next(self.our_link_state_epoch_generator)
                self.last_epoch_bump = datetime.utcnow()
                self.last_advert = datetime.fromtimestamp(0) # Force our advert to go out
            else:
                min_deadline = min(deadline, min_deadline)
        return min_deadline

    def check_hello(self, now: datetime) -> int:
        deadline = self.config.get_int("mesh.hello.interval") - (now - self.last_hello_time).seconds
        if deadline <= 0:
            self.send_hello()
            self.last_hello_time = now
            return self.config.get_int("mesh.hello.interval")
        else:
            return deadline

    def check_epoch(self, now: datetime) -> int:
        max_age = self.config.get_int("mesh.advert.max.age")
        to_delete = []
        for node, links in self.link_states.items():
            for link in list(links):
                if (now - link.created).seconds > max_age:
                    self.debug(f"Expiring link state {link} for {node}")
                    links.remove(link)
            if len(links) == 0:
                to_delete.append(node)

        for node in to_delete:
            del self.link_states[node]

        deadline = int(max_age * .80) - (now - self.last_epoch_bump).seconds
        if deadline <= 0:
            self.our_link_state_epoch = next(self.our_link_state_epoch_generator)
            self.last_epoch_bump = now
            self.send_advertisement()
            return int(max_age * .80)
        else:
            return deadline

    def check_advert(self, now: datetime) -> int:
        deadline = self.config.get_int("mesh.advert.interval") - (now - self.last_advert).seconds
        if deadline > 0:
            return deadline
        else:
            self.send_advertisement()
            self.last_advert = now
            return self.config.get_int("mesh.advert.interval")

    def check_query(self, now: datetime) -> int:
        deadline = self.config.get_int("mesh.query.interval") - (now - self.last_query).seconds
        if deadline > 0:
            return deadline
        else:
            for neighbor in self.up_neighbors():
                self.send_query(neighbor)
            self.last_query = now
            return self.config.get_int("mesh.query.interval")

    def handle_l2_payload(self, payload: L2Payload):
        """
        Handling an inbound packet from L2. We add this to the queue which wakes up the thread to process
        this packet
        """
        self.queue.put(payload)

    def process_incoming(self, payload: L2Payload):
        stream = BytesIO(payload.l3_data)

        try:
            network_header = NetworkHeader.decode(stream)
        except Exception as e:
            self.error(f"Could not decode network packet from {payload}.", e)
            return

        # Handle L3 protocols first
        if network_header.destination == self.our_address and network_header.protocol == Protocol.CONTROL:
            ctrl = ControlHeader.decode(stream)
            self.info(f"Got {ctrl} from {network_header.source}")
            if ctrl.control_type == ControlType.PING:
                self.ping_protocol.handle_ping(network_header, ctrl)
            else:
                self.warning(f"Ignoring unsupported control packet: {ctrl.control_type}")
            return

        if network_header.protocol == Protocol.HELLO:
            self.handle_hello(payload.link_id, network_header, HelloHeader.decode(stream))
            return

        if network_header.protocol == Protocol.LINK_STATE:
            self.handle_advertisement(payload.link_id, network_header, LinkStateAdvertisementHeader.decode(stream))
            return

        if network_header.protocol == Protocol.LINK_STATE_QUERY:
            self.handle_query(payload.link_id, network_header, LinkStateQueryHeader.decode(stream))
            return

        # Now decide if we should handle or drop
        if self.header_cache.contains(hash(network_header)):
            self.debug(f"Dropping duplicate {network_header}")
            return

        # If the packet is addressed to us, handle it
        if network_header.destination == self.our_address:
            self.debug(f"Handling {network_header}")
            self.l4_handlers.handle_l4(network_header, network_header.protocol, stream)
            return

        if network_header.destination == self.BroadcastAddress:
            self.debug(f"Handling broadcast {network_header}")
            self.l4_handlers.handle_l4(network_header, network_header.protocol, stream)
            if network_header.ttl > 1:
                # Decrease the TTL and re-broadcast on all links except where we heard it
                header_copy = dataclasses.replace(network_header, ttl=network_header.ttl - 1)
                stream.seek(0)
                header_copy.encode(stream)
                stream.seek(0)
                self.send(header_copy, stream.read(), exclude_link_id=payload.link_id)
            else:
                self.debug("Not re-broadcasting due to TTL")
        else:
            header_copy = dataclasses.replace(network_header, ttl=network_header.ttl - 1)
            stream.seek(0)
            header_copy.encode(stream)
            stream.seek(0)
            self.send(header_copy, stream.read(), exclude_link_id=payload.link_id)

    def handle_hello(self, link_id: int, network_header: NetworkHeader, hello: HelloHeader):
        self.debug(f"Handling hello {hello}")
        sender = network_header.source
        if network_header.source not in self.neighbors:
            self.info(f"Saw new neighbor {sender} ({hello.name})")
            self.neighbors[sender] = Neighbor(
                address=sender,
                name=hello.name,
                link_id=link_id,
                neighbors=hello.neighbors,
                last_update=datetime.utcnow(),
                state=NeighborState.INIT
            )
            self.our_link_state_epoch = next(self.our_link_state_epoch_generator)
            self.last_epoch_bump = datetime.utcnow()
        else:
            self.neighbors[sender].neighbors = hello.neighbors
            self.neighbors[sender].last_update = datetime.utcnow()

        if self.our_address in hello.neighbors:
            delay = 100
            if self.neighbors[sender].state != NeighborState.UP:
                self.info(f"Neighbor {sender} is UP!")
                self.scheduler.timer(delay, partial(self.send_query, sender), auto_start=True)
                self.neighbors[sender].state = NeighborState.UP
                delay *= 1.2
        else:
            self.info(f"Neighbor {sender} is initializing...")
            self.neighbors[sender].state = NeighborState.INIT

    def send_hello(self):
        self.debug("Sending Hello")
        hello = HelloHeader(self.config.get("host.name"), self.alive_neighbors())
        network_header = NetworkHeader(
            version=0,
            qos=QoS.Lower,
            protocol=Protocol.HELLO,
            ttl=1,
            identity=self.next_sequence(),
            length=0,
            source=self.our_address,
            destination=self.BroadcastAddress,
        )

        stream = BytesIO()
        network_header.encode(stream)
        hello.encode(stream)
        stream.seek(0)
        buffer = stream.read()
        self.send(network_header, buffer)

    def handle_advertisement(self, link_id: int, network_header: NetworkHeader, advert: LinkStateAdvertisementHeader):
        if advert.node == self.our_address:
            return

        latest_epoch = self.link_state_epochs.get(advert.node)
        if latest_epoch is None:
            self.debug(f"Initializing link state for {advert.node} with epoch {advert.epoch}")
            update = True
        else:
            epoch_cmp = lollipop_compare(latest_epoch, advert.epoch)
            update = epoch_cmp > -1
            if epoch_cmp == 1:
                self.debug(f"Updating link state for {advert.node}. "
                           f"Received epoch is {advert.epoch}, last known was {latest_epoch}")
            elif epoch_cmp == 0:
                self.debug(f"Resetting link state for {advert.node}. "
                           f"Received epoch is {advert.epoch}, last known was {latest_epoch}")
            else:
                self.debug(f"Ignoring stale link state for {advert.node}. "
                           f"Received epoch is {advert.epoch}, last known was {latest_epoch}")
        if update:
            self.link_states[advert.node] = advert.link_states
            self.link_state_epochs[advert.node] = advert.epoch
            self.host_names[advert.node] = advert.name

            # Forward this packet to all neighbors except where we heard it
            if network_header.ttl > 1:
                network_header_copy = dataclasses.replace(network_header, ttl=network_header.ttl-1)
                stream = BytesIO()
                network_header_copy.encode(stream)
                advert.encode(stream)
                stream.seek(0)
                buffer = stream.read()
                self.send(network_header_copy, buffer, exclude_link_id=link_id)

    def generate_our_adverts(self) -> List[LinkStateHeader]:
        link_states = []
        for address in self.up_neighbors():
            cost = self.link_multiplexer.get_link(self.neighbors.get(address).link_id).get_link_cost()
            link_states.append(LinkStateHeader(
                node=address,
                via=self.our_address,
                quality=cost
            ))
        return link_states

    def send_advertisement(self):
        self.debug("Sending Advertisement")

        link_states = self.generate_our_adverts()

        advertisement = LinkStateAdvertisementHeader(
            node=self.our_address,
            name=self.host_name,
            epoch=self.our_link_state_epoch,
            link_states=link_states
        )

        network_header = NetworkHeader(
            version=0,
            qos=QoS.Lower,
            protocol=Protocol.LINK_STATE,
            ttl=7,
            identity=self.next_sequence(),
            length=0,
            source=self.our_address,
            destination=self.BroadcastAddress,
        )

        stream = BytesIO()
        network_header.encode(stream)
        advertisement.encode(stream)
        stream.seek(0)
        buffer = stream.read()
        self.send(network_header, buffer)

    def handle_query(self, link_id: int, network_header: NetworkHeader, query: LinkStateQueryHeader):
        self.debug(f"Handling {query}")
        dest = network_header.source

        adverts = []
        # Check our local cache of link states
        for node, link_states in self.valid_link_states().items():
            if node == dest:
                continue

            if node not in query.link_nodes:
                # Sender is missing this node's data
                header = LinkStateAdvertisementHeader(
                    node=node,
                    name=self.host_names.get(node, "unknown"),
                    epoch=self.link_state_epochs.get(node),
                    link_states=list(self.link_states.get(node))
                )
                adverts.append(header)
            else:
                idx = query.link_nodes.index(node)
                epoch = query.link_epochs[idx]
                if lollipop_compare(epoch, self.link_state_epochs[node]) > -1:
                    header = LinkStateAdvertisementHeader(
                        node=node,
                        name=self.host_names.get(node, "unknown"),
                        epoch=self.link_state_epochs.get(node),
                        link_states=list(self.link_states.get(node))
                    )
                    adverts.append(header)

        # Include our latest state if they don't have it
        if self.our_address in query.link_nodes:
            idx = query.link_nodes.index(self.our_address)
            epoch = query.link_epochs[idx]
            if lollipop_compare(epoch, self.our_link_state_epoch) > -1:
                link_states = self.generate_our_adverts()

                our_advert = LinkStateAdvertisementHeader(
                    node=self.our_address,
                    name=self.host_name,
                    epoch=self.our_link_state_epoch,
                    link_states=link_states
                )
                adverts.append(our_advert)

        dest = network_header.source
        for advert in adverts:
            self.debug(f"Sending {advert} advert to {network_header.source}")
            resp_header = NetworkHeader(
                version=0,
                qos=QoS.Lower,
                protocol=Protocol.LINK_STATE,
                ttl=1,
                identity=self.next_sequence(),
                length=0,
                source=self.our_address,
                destination=dest,
            )

            stream = BytesIO()
            resp_header.encode(stream)
            advert.encode(stream)
            stream.seek(0)
            buffer = stream.read()
            self.send(resp_header, buffer)

    def send_query(self, neighbor: MeshAddress):
        self.debug(f"Querying {neighbor} for link states")
        known_link_states = dict()
        for node, link_states in self.valid_link_states().items():
            known_link_states[node] = self.link_state_epochs[node]

        query = LinkStateQueryHeader(
            node=self.our_address,
            epoch=self.our_link_state_epoch,
            link_nodes=list(known_link_states.keys()),
            link_epochs=list(known_link_states.values())
        )

        network_header = NetworkHeader(
            version=0,
            qos=QoS.Lower,
            protocol=Protocol.LINK_STATE_QUERY,
            ttl=1,
            identity=self.next_sequence(),
            length=0,
            source=self.our_address,
            destination=neighbor,
        )

        stream = BytesIO()
        network_header.encode(stream)
        query.encode(stream)
        stream.seek(0)
        buffer = stream.read()
        self.send(network_header, buffer)

    def route_to(self, address: MeshAddress) -> List[MeshAddress]:
        g = nx.DiGraph()
        # Other's links
        for node, link_states in self.valid_link_states().items():
            for link_state in link_states:
                g.add_weighted_edges_from([(node, link_state.node, link_state.quality)])

        # Our links
        for neighbor in self.up_neighbors():
            cost = self.link_multiplexer.get_link(self.neighbors.get(neighbor).link_id).get_link_cost()
            g.add_weighted_edges_from([(self.our_address, neighbor, cost)])
        try:
            # Compute the shortest path
            path = nx.dijkstra_path(g, self.our_address, address)

            # Ensure we have a return path
            nx.dijkstra_path(g, address, self.our_address)
            return path
        except NetworkXException:
            return []

    def send(self, header: NetworkHeader, buffer: bytes, exclude_link_id: Optional[int] = None):
        """
        Send a packet to a network destination. If the destination address is ff.ff, the packet
        is broadcast on all available L2 links (optionally excluding a given link).

        :param header the header of the packet to broadcast
        :param buffer the entire buffer of the packet to broadcast
        :param exclude_link_id an L2 link to exclude from the broadcast
        """

        if header.destination == MeshProtocol.BroadcastAddress:
            links = self.link_multiplexer.links_for_address(AX25Address("TARPN"), exclude_link_id)
        elif header.destination in self.up_neighbors():
            neighbor = self.neighbors.get(header.destination)
            links = [neighbor.link_id]
        else:
            best_route = self.route_to(header.destination)
            self.debug(f"Routing {header} via {best_route}")
            if len(best_route) > 1:
                next_hop = best_route[1]
                hop_neighbor = self.neighbors.get(next_hop)
                if hop_neighbor is not None:
                    links = [hop_neighbor.link_id]
                else:
                    self.error(f"Calculated route including {next_hop}, but we're missing that neighbor.")
                    links = []
            else:
                self.warning(f"No route to {header.destination}, dropping.")
                links = []

        for link_id in links:
            payload = L3Payload(
                source=header.source,
                destination=header.destination,
                protocol=MeshProtocol.ProtocolId,
                buffer=buffer,
                link_id=link_id,
                qos=QoS(header.qos),
                reliable=False)
            self.debug(f"Sending {payload}")
            self.link_multiplexer.offer(payload)

    def failed_send(self, neighbor: MeshAddress):
        self.debug(f"Marking neighbor {neighbor} as failed.")

    def mtu(self):
        # We want uniform packets, so get the min L2 MTU
        return self.link_multiplexer.mtu() - MeshProtocol.HeaderBytes

    def route_packet(self, address: L3Address) -> Tuple[bool, int]:
        # Subtract L3 header size and multiply by max fragments
        l3_mtu = MeshProtocol.MaxFragments * (self.mtu() - MeshProtocol.HeaderBytes)
        if address == MeshProtocol.BroadcastAddress:
            return True, l3_mtu
        else:
            path = self.route_to(cast(MeshAddress, address))
            return len(path) > 0, l3_mtu

    def send_packet(self, payload: L3Payload) -> bool:
        return self.link_multiplexer.offer(payload)

    def listen(self, address: MeshAddress):
        # By default we listen for all addresses
        pass

    def register_transport_protocol(self, protocol) -> None:
        # TODO remove this from L3Protocol
        pass
