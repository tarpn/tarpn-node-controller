import unittest

from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.header import DatagramHeader, Datagram
from tarpn.network.mesh.protocol import MeshProtocol
from tarpn.transport import L4Protocol
from ..utils import MockLinkMultiplexer, MockScheduler, MockTime


class TestBroadcast(unittest.TestCase):
    def setUp(self) -> None:
        self.time = MockTime(0)
        self.scheduler = MockScheduler(self.time)

        # Node 1 -- Alice
        self.multi_1 = MockLinkMultiplexer()
        self.node_1 = MeshProtocol(self.time, MeshAddress(1), self.multi_1, self.scheduler)

        # Node 2 -- Bob
        self.multi_2 = MockLinkMultiplexer()
        self.node_2 = MeshProtocol(self.time, MeshAddress(2), self.multi_2, self.scheduler)

        # Node 3 -- Carol
        self.multi_3 = MockLinkMultiplexer()
        self.node_3 = MeshProtocol(self.time, MeshAddress(3), self.multi_3, self.scheduler)

        # Node 4 -- Dan
        self.multi_4 = MockLinkMultiplexer()
        self.node_4 = MeshProtocol(self.time, MeshAddress(4), self.multi_4, self.scheduler)

        # link ids need to be shared for forwarding to work
        self.multi_1.attach_neighbor(1, self.node_2)
        self.multi_1.attach_neighbor(3, self.node_3)

        self.multi_2.attach_neighbor(1, self.node_1)
        self.multi_2.attach_neighbor(2, self.node_3)

        self.multi_3.attach_neighbor(2, self.node_2)
        self.multi_3.attach_neighbor(3, self.node_1)
        self.multi_3.attach_neighbor(4, self.node_4)

        self.multi_4.attach_neighbor(4, self.node_3)

    def test_send_receive(self):
        msg = "Hello, Node 3".encode("utf-8")
        header = DatagramHeader(source=100, destination=100, length=len(msg), checksum=0)

        captured = None
        class MockTransportManager(L4Protocol):
            def handle_datagram(s, datagram: Datagram):
                nonlocal captured
                captured = datagram

        self.node_4.register_transport_protocol(MockTransportManager())
        self.node_1.send_datagram(MeshAddress(4), header, msg)

        self.assertEqual(captured.payload.decode("utf-8"), "Hello, Node 3")


