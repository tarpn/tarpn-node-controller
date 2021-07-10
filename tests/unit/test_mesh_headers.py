import unittest
from io import BytesIO

from tarpn.crc import crc_b
from tarpn.network import QoS
from tarpn.network.mesh.header import NetworkHeader, Protocol, MeshAddress, DatagramHeader, \
    FragmentHeader, FragmentFlags
from tarpn.network.mesh.protocol import encode_packet
from tarpn.util import TTLCache
from ..utils import MockTime


class TestMeshHeaders(unittest.TestCase):
    def test_ttl_cache(self):
        time = MockTime()
        cache = TTLCache(time, 10)

        header = NetworkHeader(
            version=0,
            protocol=Protocol.DATAGRAM,
            qos=QoS.Default,
            ttl=4,
            identity=42,
            length=0,
            source=MeshAddress(1),
            destination=MeshAddress(2))

        self.assertFalse(cache.contains(hash(header)))
        self.assertTrue(cache.contains(hash(header)))

        same_header = NetworkHeader(
            version=0,
            protocol=Protocol.DATAGRAM,
            qos=QoS.Default,
            ttl=4,
            identity=42,
            length=0,
            source=MeshAddress(1),
            destination=MeshAddress(2))

        self.assertTrue(cache.contains(hash(same_header)))

        diff_header = NetworkHeader(
            version=0,
            protocol=Protocol.DATAGRAM,
            qos=QoS.Default,
            ttl=4,
            identity=43,
            length=0,
            source=MeshAddress(1),
            destination=MeshAddress(2))

        self.assertFalse(cache.contains(hash(diff_header)))
        self.assertTrue(cache.contains(hash(diff_header)))

        time.sleep(11)
        self.assertFalse(cache.contains(hash(header)))
        self.assertFalse(cache.contains(hash(diff_header)))

    def test_encode_decode_datagram(self):
        msg = "Hello, World!".encode("utf-8")
        datagram_header_1 = DatagramHeader(
            source=100,
            destination=100,
            length=len(msg),
            checksum=crc_b(msg))

        header1 = NetworkHeader(
            version=0,
            protocol=Protocol.DATAGRAM,
            qos=QoS.Default,
            ttl=4,
            identity=42,
            length=datagram_header_1.size() + len(msg),
            source=MeshAddress(1),
            destination=MeshAddress(2))

        data = encode_packet(header1, [datagram_header_1], msg)
        stream = BytesIO(data)
        header2 = NetworkHeader.decode(stream)
        datagram2 = DatagramHeader.decode(stream)
        self.assertEqual(header1, header2)
        self.assertEqual(datagram_header_1, datagram2)

    def test_encode_decode_fragment(self):
        msg = "Hello, World!".encode("utf-8")
        datagram1 = DatagramHeader(
            source=100,
            destination=100,
            length=len(msg),
            checksum=crc_b(msg))

        fragment1 = FragmentHeader(Protocol.DATAGRAM, FragmentFlags.NONE, 0, 99)

        header1 = NetworkHeader(
            version=0,
            protocol=Protocol.FRAGMENT,
            qos=QoS.Default,
            ttl=4,
            identity=42,
            length=fragment1.size() + datagram1.size() + len(msg),
            source=MeshAddress(1),
            destination=MeshAddress(2))

        stream = BytesIO()
        header1.encode(stream)
        fragment1.encode(stream)
        datagram1.encode(stream)
        stream.write(msg)
        stream.seek(0)

        header2 = NetworkHeader.decode(stream)
        fragment = FragmentHeader.decode(stream)
        datagram = DatagramHeader.decode(stream)
        msg2 = stream.read()
        self.assertEqual(header1, header2)
        self.assertEqual(datagram1, datagram)
        self.assertEqual(msg2, msg)
