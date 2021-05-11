import unittest
from typing import Dict, Optional

from tarpn.datalink import L2Address, L2Payload, FrameData
from tarpn.datalink.ax25_l2 import AX25Address
from tarpn.datalink.protocol import L2Protocol, LinkMultiplexer
from tarpn.network import L3Payload, L3Protocol
from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.header import DatagramHeader, Datagram
from tarpn.network.mesh.protocol import MeshProtocol
from tarpn.transport import L4Protocol
from ..utils import MockLinkMultiplexer, MockScheduler, MockTime


class MockL2Protocol(L2Protocol):
    def __init__(self, device_id: int, link_multiplexer: LinkMultiplexer):
        self.device_id = device_id
        self.link_multiplexer = link_multiplexer
        self.logical_links: Dict[L2Address, int] = {}
        self.connected_to: Optional[L2Protocol] = None
        self.l3: Optional[L3Protocol] = None

    def connect_to(self, other: L2Protocol):
        self.connected_to = other

    def attach_l3(self, l3: L3Protocol):
        self.l3 = l3

    def get_device_id(self) -> int:
        return self.device_id

    def maximum_transmission_unit(self) -> int:
        return 256

    def maybe_open_link(self, address: L2Address) -> int:
        if address in self.logical_links:
            return self.logical_links[address]
        else:
            link_id = self.link_multiplexer.add_link(self)
            self.logical_links[address] = link_id
            return link_id

    def get_link_address(self) -> L2Address:
        pass

    def get_peer_address(self, link_id) -> L2Address:
        pass

    def peer_connected(self, link_id) -> bool:
        return self.connected_to is not None

    def receive_frame(self, frame: FrameData) -> None:
        if self.l3 is not None:
            self.l3.handle_l2_payload(L2Payload(link_id=0,
                                                source=L2Address(),
                                                destination=L2Address(),
                                                l3_protocol=MeshProtocol.ProtocolId,
                                                l3_data=frame.data))

    def handle_queue_full(self) -> None:
        pass

    def maximum_frame_size(self) -> int:
        pass

    def send_packet(self, packet: L3Payload) -> bool:
        if self.connected_to is not None:
            self.connected_to.receive_frame(FrameData(port=self.device_id, data=packet.buffer))
            return True
        else:
            return False


class TestBroadcast(unittest.TestCase):
    def setUp(self) -> None:
        self.time = MockTime(0)
        self.scheduler = MockScheduler(self.time)

        # Node 1 -- Alice
        self.multi_1 = MockLinkMultiplexer()
        self.l2_1 = MockL2Protocol(1, self.multi_1)
        self.multi_1.register_device(self.l2_1)
        self.node_1 = MeshProtocol(self.time, MeshAddress(1), self.multi_1, self.scheduler)
        self.l2_1.attach_l3(self.node_1)

        # Node 2 -- Bob
        self.multi_2 = MockLinkMultiplexer()
        self.l2_2 = MockL2Protocol(1, self.multi_2)
        self.multi_2.register_device(self.l2_2)
        self.node_2 = MeshProtocol(self.time, MeshAddress(2), self.multi_2, self.scheduler)
        self.l2_2.attach_l3(self.node_2)

        # Node 3 -- Carol
        self.multi_3 = MockLinkMultiplexer()
        self.l2_3 = MockL2Protocol(1, self.multi_3)
        self.multi_3.register_device(self.l2_3)
        self.node_3 = MeshProtocol(self.time, MeshAddress(3), self.multi_3, self.scheduler)
        self.l2_3.attach_l3(self.node_3)

        self.l2_1.connect_to(self.l2_2)
        self.l2_2.connect_to(self.l2_3)

    def test_send_receive(self):
        import logging
        logging.basicConfig(level=logging.DEBUG)
        msg = "Hello, Node 3".encode("utf-8")
        header = DatagramHeader(source=100, destination=100, length=len(msg), checksum=0)

        class MockTransportManager(L4Protocol):
            def handle_datagram(self, datagram: Datagram):
                print(datagram)

        self.node_3.register_transport_protocol(MockTransportManager())

        self.node_1.send_datagram(MeshAddress(3), header, msg)
        link_id_1 = self.l2_1.maybe_open_link(AX25Address("TAPRN"))
        self.multi_1.poll(link_id_1)

        link_id_2 = self.l2_2.maybe_open_link(AX25Address("TAPRN"))
        self.multi_2.poll(link_id_2)

        link_id_3 = self.l2_3.maybe_open_link(AX25Address("TAPRN"))
        self.multi_3.poll(link_id_3)
