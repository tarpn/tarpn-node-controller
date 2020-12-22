import asyncio
import unittest

from tarpn.ax25 import *
from tarpn.ax25.datalink import DataLinkManager
from tarpn.ax25.statemachine import AX25StateEvent
from tarpn.ax25 import AX25StateType
from tarpn.port.kiss import decode_kiss_frame


class TestAX25(unittest.IsolatedAsyncioTestCase):
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

    async def test_connect(self):
        in_queue = asyncio.Queue()
        out_queue = asyncio.Queue()
        ax25 = DataLinkManager(AX25Call("K4DBZ", 1), 0, in_queue, out_queue)

        # TODO don't do this here
        ax25.state_machine._get_or_create_session(AX25Call("K4DBZ", 2), AX25Call("K4DBZ", 1))

        dl_connect = AX25StateEvent.dl_connect(AX25Call("K4DBZ", 2), AX25Call("K4DBZ", 1))
        ax25.state_machine.handle_internal_event(dl_connect)

        state = ax25.state_machine._sessions["K4DBZ-2"]
        assert state.current_state == AX25StateType.AwaitingConnection

    async def test_timeout(self):
        loop = asyncio.get_event_loop()

        # TEST-1
        in_queue_1 = asyncio.Queue()
        out_queue_1 = asyncio.Queue()
        ax25_1 = DataLinkManager(AX25Call("TEST", 1), 0, in_queue_1, out_queue_1, loop.create_future)

        # TEST-2
        in_queue_2 = asyncio.Queue()
        out_queue_2 = asyncio.Queue()
        ax25_2 = DataLinkManager(AX25Call("TEST", 2), 0, in_queue_2, out_queue_2, loop.create_future)

        # TEST-2 connecting to TEST-1
        ax25_2.dl_connect_request(AX25Call("TEST", 1))

        sabm = await out_queue_2.get()
        await in_queue_1.put(sabm)
        await ax25_1._loop()

        ua = await out_queue_1.get()
        await in_queue_2.put(ua)
        await ax25_2._loop()

        ax25_2.dl_data_request(AX25Call("TEST", 1), L3Protocol.NoLayer3, "Testing".encode("utf-8"))

    async def test_link(self):
        loop = asyncio.get_event_loop()

        # TEST-1
        in_queue_1 = asyncio.Queue()
        out_queue_1 = asyncio.Queue()
        ax25_1 = DataLinkManager(AX25Call("TEST", 1), 0, in_queue_1, out_queue_1, loop.create_future)

        # TEST-2
        in_queue_2 = asyncio.Queue()
        out_queue_2 = asyncio.Queue()
        ax25_2 = DataLinkManager(AX25Call("TEST", 2), 0, in_queue_2, out_queue_2, loop.create_future)

        # TEST-2 connecting to TEST-1
        ax25_2.dl_connect_request(AX25Call("TEST", 1))

        # Connect the queues
        async def bridge(source: asyncio.Queue, sink: asyncio.Queue):
            while True:
                frame = await source.get()
                await sink.put(frame)
                source.task_done()
        asyncio.create_task(bridge(out_queue_1, in_queue_2))
        asyncio.create_task(bridge(out_queue_2, in_queue_1))

        # Start the run loop
        asyncio.create_task(ax25_1.start())
        asyncio.create_task(ax25_2.start())

        async def connected():
            while True:
                if ax25_2.state_machine._sessions["TEST-1"].current_state == AX25StateType.Connected:
                    break
                await asyncio.sleep(0.010)
            return True

        await connected()

        assert ax25_1.state_machine._sessions["TEST-2"].current_state == AX25StateType.Connected
        assert ax25_2.state_machine._sessions["TEST-1"].current_state == AX25StateType.Connected
