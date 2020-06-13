import asyncio
import unittest
from typing import cast

from tarpn.app import Logger, Context, Application
from tarpn.ax25 import *
from tarpn.ax25.datalink import DataLink
from tarpn.ax25.statemachine import AX25StateEvent, AX25StateType
from tarpn.frame import DataLinkMultiplexer
from tarpn.port.kiss import decode_kiss_frame


class Echo(Application):
    def on_connect(self, context: Context):
        print(f"ECHO Connected to {context.remote_address()}")

    def on_disconnect(self, context: Context):
        print(f"ECHO Disconnected from {context.remote_address()}")

    def on_error(self, context: Context, error: str):
        print(f"ECHO Error: {error}")

    def read(self, context: Context, data: bytes):
        print("ECHO Got: " + data.decode("ASCII"))
        context.write(data)


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
        dlm = DataLinkMultiplexer()
        dlm.add_port(0, out_queue)
        ax25 = DataLink(in_queue, dlm, Logger())

        # TODO don't do this here
        ax25.state_machine._get_or_create_session(AX25Call("K4DBZ", 2), AX25Call("K4DBZ", 1))

        dl_connect = AX25StateEvent.dl_connect(AX25Call("K4DBZ", 2), AX25Call("K4DBZ", 1))
        ax25.state_machine.handle_internal_event(dl_connect)

        state = ax25.state_machine._sessions["K4DBZ-2"]
        assert state.current_state == AX25StateType.AwaitingConnection

        frame = cast(UFrame, asyncio.get_event_loop().run_until_complete(out_queue.get()))
        assert frame.u_type == UnnumberedType.SABM

    def test_link(self):
        # TEST-1
        in_queue_1 = asyncio.Queue()
        out_queue_1 = asyncio.Queue()
        dlm_1 = DataLinkMultiplexer()
        dlm_1.add_port(0, out_queue_1)
        ax25_1 = DataLink(in_queue_1, dlm_1, Logger())
        ax25_1.bind_l2_application(AX25Call("TEST", 1), Echo())

        # TEST-2
        in_queue_2 = asyncio.Queue()
        out_queue_2 = asyncio.Queue()
        dlm_2 = DataLinkMultiplexer()
        dlm_2.add_port(0, out_queue_2)
        ax25_2 = DataLink(in_queue_2, dlm_2, Logger())

        # TEST-2 connecting to TEST-1
        dl_connect = AX25StateEvent.dl_connect(AX25Call("TEST", 1), AX25Call("TEST", 2))
        ax25_2.state_machine._get_or_create_session(AX25Call("TEST", 1), AX25Call("TEST", 2))
        ax25_2.state_machine.handle_internal_event(dl_connect)
        ax25_2.last_seen_on_port[AX25Call("TEST", 1)] = 0  # TODO fix this

        # Connect the queues
        async def bridge(source: asyncio.Queue, sink: asyncio.Queue):
            while True:
                frame = await source.get()
                await sink.put(frame)
                source.task_done()
        asyncio.ensure_future(bridge(out_queue_1, in_queue_2))
        asyncio.ensure_future(bridge(out_queue_2, in_queue_1))

        # Start the run loop
        asyncio.ensure_future(ax25_1.start())
        asyncio.ensure_future(ax25_2.start())
        loop = asyncio.get_event_loop()

        async def connected():
            while True:
                if ax25_2.state_machine._sessions["TEST-1"].current_state == AX25StateType.Connected:
                    break
                await asyncio.sleep(0.010)
            return True

        loop.run_until_complete(connected())

        assert ax25_1.state_machine._sessions["TEST-2"].current_state == AX25StateType.Connected
        assert ax25_2.state_machine._sessions["TEST-1"].current_state == AX25StateType.Connected
