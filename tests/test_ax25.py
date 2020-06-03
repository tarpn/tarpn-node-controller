import asyncio
import unittest
from typing import cast

from tarpn.ax25 import decode_ax25_packet, L3Protocol, AX25Call, SupervisoryType, SFrame, SupervisoryCommand, \
    AX25Packet, DummyPacket, UFrame, UnnumberedType
from tarpn.ax25.sm import AX25StateEvent, AX25EventType, AX25StateType
from tarpn.kiss import decode_kiss_frame
from tarpn.test import AX25Impl


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
        assert packet.protocol == L3Protocol.NetRom
        assert packet.dest == AX25Call("K4DBZ", 2)
        assert packet.source == AX25Call("K4DBZ", 1)

    def test_s_frame(self):
        s_frame = SFrame.s_frame(AX25Call("K4DBZ", 2), AX25Call("K4DBZ", 1), [],
                                 SupervisoryCommand.Command, SupervisoryType.RR, 5, True)
        packet = decode_ax25_packet(s_frame.buffer)
        assert s_frame == packet

    def test_connect(self):
        in_queue = asyncio.Queue()
        out_queue = asyncio.Queue()
        ax25 = AX25Impl(in_queue, out_queue)

        dummy = DummyPacket.dummy(AX25Call("K4DBZ", 2), AX25Call("K4DBZ", 1))
        dl_connect = AX25StateEvent(AX25Call("K4DBZ", 2), dummy, AX25EventType.DL_CONNECT)
        ax25.state_machine.handle_internal_event(dl_connect)

        state = ax25.state_machine._sessions["K4DBZ-1"]
        assert state.current_state == AX25StateType.AwaitingConnection

        packet = cast(UFrame, asyncio.get_event_loop().run_until_complete(out_queue.get()))
        assert packet.u_type == UnnumberedType.SABM
