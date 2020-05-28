import unittest

from tarpn.ax25 import decode_ax25_packet, Protocol, AX25Call, SupervisoryType, SFrame, SupervisoryCommand
from tarpn.kiss import decode_kiss_frame


class TestAX25(unittest.TestCase):
    def test_parse(self):
        data = bytes([0, 150, 104, 136, 132, 180, 64, 228, 150, 104, 136, 132, 180, 64, 115, 17])
        frame = decode_kiss_frame(data)
        packet = decode_ax25_packet(frame.data)
        print(packet)
        assert packet.dest == AX25Call("K4DBZ", 2)
        assert packet.source == AX25Call("K4DBZ", 9)
        assert packet.control_type == SupervisoryType.RR

    def test_parse_netrom(self):
        data = bytes([0, 150, 104, 136, 132, 180, 64, 228, 150, 104, 136, 132, 180, 64, 99, 0, 207, 150, 104,
                      136, 132, 180, 64, 98, 150, 104, 136, 132, 180, 64, 4, 7, 1, 132, 0, 0, 1, 2, 150, 104, 136,
                      132, 180, 64, 96, 150, 104, 136, 132, 180, 64, 98, 180, 0])
        frame = decode_kiss_frame(data)
        packet = decode_ax25_packet(frame.data)
        print(packet)
        assert packet.protocol == Protocol.NetRom
        assert packet.dest == AX25Call("K4DBZ", 2)
        assert packet.source == AX25Call("K4DBZ", 1)

    def test_s_frame(self):
        s_frame = SFrame.build(AX25Call("K4DBZ", 2), AX25Call("K4DBZ", 1), [],
                               SupervisoryCommand.Command, SupervisoryType.RR, 5, True)
        packet = decode_ax25_packet(s_frame.buffer)
        assert s_frame == packet
