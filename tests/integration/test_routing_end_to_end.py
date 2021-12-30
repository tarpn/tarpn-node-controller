import logging
import unittest
from io import BytesIO
from typing import Callable

from tarpn.network import QoS
from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.header import DatagramHeader, NetworkHeader, Protocol
from tarpn.network.mesh.protocol import MeshProtocol
from tarpn.settings import NetworkConfig
from tarpn.transport.mesh import L4Handlers, L4Handler
from ..utils import MockLinkMultiplexer, MockScheduler, MockTime


class TestBroadcast(unittest.TestCase):

    def build_config(self, address: str, host_name: str) -> NetworkConfig:
        return NetworkConfig.from_dict({
            "mesh.address": address,
            "host.name": host_name,
            "mesh.dead.interval": "10",
            "mesh.hello.interval": "10",
            "mesh.advert.max.age": "10",
            "mesh.advert.interval": "10",
            "mesh.query.interval": "10"
        })

    def setUp(self) -> None:
        logging.basicConfig(level=logging.DEBUG)

        self.time = MockTime(0)
        self.scheduler = MockScheduler(self.time)

        # Node 1 -- Alice
        self.multi_1 = MockLinkMultiplexer()
        self.config_1 = self.build_config("00.01", "alice")
        self.node_1 = MeshProtocol(self.time, self.config_1, self.multi_1, L4Handlers(), self.scheduler)

        # Node 2 -- Bob
        self.multi_2 = MockLinkMultiplexer()
        self.config_2 = self.build_config("00.02", "bob")
        self.node_2 = MeshProtocol(self.time, self.config_2, self.multi_2, L4Handlers(), self.scheduler)

        # Node 3 -- Carol
        self.multi_3 = MockLinkMultiplexer()
        self.config_3 = self.build_config("00.03", "carol")
        self.node_3 = MeshProtocol(self.time, self.config_3, self.multi_3, L4Handlers(), self.scheduler)

        # Node 4 -- Dan
        self.multi_4 = MockLinkMultiplexer()
        self.config_4 = self.build_config("00.04", "dan")
        self.node_4 = MeshProtocol(self.time, self.config_4, self.multi_4, L4Handlers(), self.scheduler)

        # link ids need to be shared for forwarding to work
        self.multi_1.attach_neighbor(1, self.node_2)
        self.multi_1.attach_neighbor(3, self.node_3)

        self.multi_2.attach_neighbor(1, self.node_1)
        self.multi_2.attach_neighbor(2, self.node_3)

        self.multi_3.attach_neighbor(2, self.node_2)
        self.multi_3.attach_neighbor(3, self.node_1)
        self.multi_3.attach_neighbor(4, self.node_4)

        self.multi_4.attach_neighbor(4, self.node_3)

        self.for_each_node(self.setupNode)

        self.for_each_node(MeshProtocol.send_hello)
        self.for_each_node(self.drain_queue)
        self.for_each_node(MeshProtocol.send_hello)
        self.for_each_node(self.drain_queue)

        self.for_each_node(MeshProtocol.send_advertisement)
        self.for_each_node(self.drain_queue)

        self.for_each_node(MeshProtocol.send_advertisement)
        self.for_each_node(self.drain_queue)


    def for_each_node(self, fn: Callable[[MeshProtocol], None]):
        for node in [self.node_1, self.node_2, self.node_3, self.node_4]:
            fn(node)
            self.time.sleep(1)

    def setupNode(self, mesh: MeshProtocol):
        mesh.deadline = lambda now: 0

    def drain_queue(self, mesh: MeshProtocol):
        while mesh.iter_loop():
            continue


    def test_send_receive(self):
        network_header = NetworkHeader(
            version=0,
            qos=QoS.Lower,
            protocol=Protocol.DATAGRAM,
            ttl=3,
            identity=10,
            length=0,
            source=MeshAddress(1),
            destination=MeshAddress(4),
        )

        msg = "Hello, Node 4".encode("utf-8")
        datagram_header = DatagramHeader(source=100, destination=100, length=len(msg), checksum=0)
        stream = BytesIO()
        network_header.encode(stream)
        datagram_header.encode(stream)
        stream.write(msg)
        stream.seek(0)

        captured = None
        class MockTransportManager(L4Handler):
            def handle_l4(self, network_header: NetworkHeader, stream: BytesIO):
                nonlocal captured
                DatagramHeader.decode(stream)
                captured = stream.read()

        self.node_4.l4_handlers.handlers[Protocol.DATAGRAM] = MockTransportManager()
        self.node_1.send(network_header, stream.read())
        self.for_each_node(self.drain_queue)
        self.assertEqual(captured.decode("utf-8"), "Hello, Node 4")