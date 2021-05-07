import unittest
from typing import Dict, Optional

from tarpn.datalink import L2Address, L2Payload, FrameData
from tarpn.datalink.protocol import L2Protocol, LinkMultiplexer
from tarpn.network import L3Payload, L3Protocol
from tarpn.network.broadcast_l3 import NetworkProtocol, Address
from tests.utils import MockLinkMultiplexer, MockScheduler, MockTime


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
                                                l3_protocol=NetworkProtocol.ProtocolId,
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
        self.node_1 = NetworkProtocol(Address(1), self.multi_1, self.scheduler)

        # Node 2 -- Bob
        self.multi_2 = MockLinkMultiplexer()
        self.l2_2 = MockL2Protocol(1, self.multi_2)
        self.multi_2.register_device(self.l2_2)
        self.node_2 = NetworkProtocol(Address(2), self.multi_2, self.scheduler)

        # Node 3 -- Carol
        self.multi_3 = MockLinkMultiplexer()
        self.l2_3 = MockL2Protocol(1, self.multi_3)
        self.multi_3.register_device(self.l2_3)
        self.node_3 = NetworkProtocol(Address(3), self.multi_3, self.scheduler)

        self.l2_1.connect_to(self.l2_2)

    def test_send_receive(self):
        self.node_1.announce_self()

        #node_1_link_id = self.l2_1.maybe_open_link(AX25Address("TAPRN"))
        #packet = self.multi_1.get_queue(node_1_link_id).maybe_take()
        #self.assertEqual(packet.protocol, BroadcastProtocol.ProtocolId)

        #node_2_link_id = self.l2_2.maybe_open_link(AX25Address("TAPRN"))

        self.assertEqual(self.node_2.delivered.get(1), 1)
