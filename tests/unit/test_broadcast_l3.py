import unittest

from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.header import DatagramHeader, PacketHeader
from tarpn.network.mesh.protocol import MeshProtocol
from tests.utils import MockScheduler, MockTime


class TestBroadcast(unittest.TestCase):
    def test_fragment(self):
        msg = """
Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore 
et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut 
aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum 
dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui 
officia deserunt mollit anim id est laborum""".replace("\r", "").encode("utf-8")
        datagram = DatagramHeader(100, 100, len(msg), 0)

        l3 = MeshProtocol(MockTime(), MeshAddress(1), None, MockScheduler(MockTime()))

        captured = []

        def broadcast_capture(header: PacketHeader, buffer: bytes, *args, **kwargs):
            captured.append((header, buffer))

        l3.broadcast = broadcast_capture

        l3._max_fragment_size = lambda: 128

        l3.send_datagram(MeshAddress(2), datagram, msg)
        print(captured)

