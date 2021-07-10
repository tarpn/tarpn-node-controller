import dataclasses
import threading
from collections import defaultdict, deque
from io import BytesIO
from typing import Dict

from tarpn.log import LoggingMixin
from tarpn.network import QoS
from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.header import NetworkHeader, ReliableHeader, ReliableFlags, Protocol
from tarpn.network.mesh.protocol import MeshProtocol, encode_packet
from tarpn.scheduler import Scheduler
from tarpn.transport.mesh import L4Handler, L4Handlers
from tarpn.util import backoff


@dataclasses.dataclass
class ReliableItem:
    header: NetworkHeader
    sequence: int
    buffer: bytes
    attempt: int = 0


class ReliableChannel(LoggingMixin):
    MaxAckPending = 4

    def __init__(self, neighbor: MeshAddress, network: MeshProtocol, scheduler: Scheduler):
        LoggingMixin.__init__(self)
        self.network = network
        self.scheduler = scheduler

        self.mutex = threading.Lock()
        self.not_empty = threading.Condition(self.mutex)
        self.not_full = threading.Condition(self.mutex)

        self.sent: deque[ReliableItem] = deque()
        self.timer = scheduler.timer(3_000, self.check_acks)
        self.backoff = backoff(1_000, 1.2, 5_000)

        self.neighbor = neighbor
        self.seq: int = 0


class ReliableManager(LoggingMixin):
    MaxAckPending = 4

    def __init__(self, network: MeshProtocol, scheduler: Scheduler):
        LoggingMixin.__init__(self)
        self.network = network
        self.scheduler = scheduler

        self.mutex = threading.Lock()
        self.not_empty = threading.Condition(self.mutex)
        self.not_full = threading.Condition(self.mutex)

        self.sent: deque[ReliableItem] = deque()
        self.timer = scheduler.timer(3_000, self.check_acks)
        self.backoff = backoff(1_000, 1.2, 5_000)

        self.seq_by_neighbor: Dict[MeshAddress, int] = defaultdict(int)

    def send(self, network_header: NetworkHeader, buffer: bytes):
        with self.mutex:
            sequence = self.seq_by_neighbor[network_header.destination]
            self.seq_by_neighbor[network_header.destination] += 1

        reliable_header = ReliableHeader(
            protocol=network_header.protocol,
            flags=ReliableFlags.NONE,
            sequence=sequence
        )

        network_header = dataclasses.replace(network_header, protocol=Protocol.RELIABLE)
        buffer = encode_packet(network_header, [reliable_header], buffer)

        with self.not_full:
            if len(self.sent) > ReliableManager.MaxAckPending:
                while len(self.sent) >= ReliableManager.MaxAckPending:
                    self.not_full.wait()
            item = ReliableItem(
                network_header,
                sequence,
                buffer,
                0
            )
            self.sent.append(item)
            self.info(f"Sending {item}")
            self.not_empty.notify()

        self.network.send(network_header, buffer)
        if not self.timer.running():
            self.timer.start()

    def handle_ack(self, neighbor: MeshAddress, sequence: int):
        # lock
        with self.not_empty:
            if len(self.sent) == 0:
                self.warning(f"Received ACK for {neighbor, self}, but no ACKs were pending")
                return

            for item in self.sent:
                if (neighbor, sequence) == (item.header.destination, item.sequence):
                    self.info(f"Got ack for {item}")
                    self.sent.remove(item)
                    self.not_full.notify()
                    self.timer.reset()
                    break

            if len(self.sent) == 0:
                self.timer.cancel()
                self.timer.delay = 3_000

    def check_acks(self):
        with self.not_empty:
            while len(self.sent) == 0:
                self.not_empty.wait()

            item = self.sent[0]
            item.attempt += 1
            if item.attempt > 3:
                self.info(f"Expiring {item}")
                self.sent.remove(item)
                self.not_full.notify()
            else:
                self.info(f"Resending {item}")
                stream = BytesIO(item.buffer)
                network_header = NetworkHeader.decode(stream)
                network_header = dataclasses.replace(network_header, identity=self.network.next_sequence())
                stream.seek(0)
                network_header.encode(stream)
                stream.seek(0)
                self.network.send(network_header, stream.read())
                # TODO backoff
            self.timer.reset()


class ReliableProtocol(L4Handler, LoggingMixin):
    """
    Manage acknowledgments for reliability layer.
    """

    def __init__(self, network: MeshProtocol, reliable_manager: ReliableManager, handlers: L4Handlers):
        LoggingMixin.__init__(self)
        self.network = network
        self.reliable_manager = reliable_manager
        self.handlers = handlers

    def handle_l4(self, network_header: NetworkHeader, stream: BytesIO):
        reliable_header = ReliableHeader.decode(stream)
        self.debug(f"Handling reliable {reliable_header}")

        if reliable_header.flags & ReliableFlags.ACK:
            # We received an ACK
            self.reliable_manager.handle_ack(network_header.source, reliable_header.sequence)
        else:
            # An ACK is being requested
            if network_header.source not in self.network.neighbors():
                # Ignore if we haven't seen this neighbor before, we need discovery first
                # TODO send discovery to this neighbor
                self.debug(f"Ignoring {reliable_header} since we don't know this neighbor.")
                return
            network_header = NetworkHeader(
                version=0,
                qos=QoS.Default,
                protocol=Protocol.RELIABLE,
                ttl=1,
                identity=self.network.next_sequence(),
                length=0,
                source=self.network.our_address,
                destination=network_header.source,
            )
            ack_header = ReliableHeader(
                protocol=Protocol.NONE,
                flags=ReliableFlags.ACK,
                sequence=reliable_header.sequence,
            )
            response = BytesIO()
            network_header.encode(response)
            ack_header.encode(response)
            response.seek(0)
            buffer = response.read()
            self.info(f"Sending Ack for {(network_header.source, reliable_header.sequence)}")
            self.network.send(network_header, buffer)
            self.handlers.handle_l4(network_header, reliable_header.protocol, stream)

    def send(self, header: NetworkHeader, reliable_header: ReliableHeader, buffer: bytes):
        self.reliable_manager.send(header, buffer)
