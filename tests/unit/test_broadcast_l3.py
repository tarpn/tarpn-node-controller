import unittest

from tarpn.network.mesh_l3 import *
from tests.utils import MockScheduler, MockTime


class TestBroadcast(unittest.TestCase):
    def test_fragment_1(self):
        s = """
Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore 
et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut 
aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum 
dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui 
officia deserunt mollit anim id est laborum""".replace("\r", "")
        datagram = DatagramPDU.create(MeshAddress(2), MeshAddress(255), 100, s.encode("utf-8"))

        l3 = NetworkProtocol(MeshAddress(1), None, MockScheduler(MockTime()))
        captured = []
        l3.broadcast = captured.append
        l3._max_fragment_size = lambda: 200
        l3.send_datagram(datagram)
        joined = bytearray()
        for fragment in captured:
            joined.extend(fragment.payload_data)
        datagram = DatagramPDU.parse(MeshAddress(2), MeshAddress(255), joined)
        self.assertEqual(datagram.data, s.encode("utf-8"))

    def test_fragments(self):
        datagram = DatagramPDU.create(MeshAddress(2), MeshAddress(255), 42, "hello, testing 123".encode("utf-8"))
        buffer = bytearray()
        DatagramPDU.encode(datagram, buffer)
        l3 = NetworkProtocol(MeshAddress(1), None, MockScheduler(MockTime()))
        l3.maybe_deliver_local(PacketDeprecated(
            version=0,
            flags=Flags.NONE,
            qos=0,
            source=2,
            dest=255,
            seq=0,
            frag=2,
            ttl=2,
            protocol=Protocol.DATAGRAM,
            payload_data=buffer[7:]
        ))
        l3.maybe_deliver_local(PacketDeprecated(
            version=0,
            flags=Flags.FRAGMENT,
            qos=0,
            source=2,
            dest=255,
            seq=1022,
            frag=0,
            ttl=2,
            protocol=Protocol.DATAGRAM,
            payload_data=buffer[0:3]
        ))
        l3.maybe_deliver_local(PacketDeprecated(
            version=0,
            flags=Flags.FRAGMENT,
            qos=0,
            source=2,
            dest=255,
            seq=1023,
            frag=1,
            ttl=2,
            protocol=Protocol.DATAGRAM,
            payload_data=buffer[3:7]
        ))

        self.assertEqual(l3.buffer[2].get(10), None)
