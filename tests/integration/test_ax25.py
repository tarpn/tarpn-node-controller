import asyncio
import logging
import sys
import time
import unittest
from functools import partial
from typing import cast

from tarpn.ax25 import AX25Call, AX25StateType, L3Protocol, L3Handler
from tarpn.ax25.datalink import DataLinkManager
from tarpn.port.kiss import KISSProtocol
from tests import utils


class TestTwoNodes(unittest.IsolatedAsyncioTestCase):
    """
    Create a simple two node network
    """
    def setUp(self):
        loop = asyncio.get_event_loop()
        in_queue_1 = asyncio.Queue()
        out_queue_1 = asyncio.Queue()
        protocol_factory = partial(KISSProtocol, loop, in_queue_1, out_queue_1, port_id=1, check_crc=False)
        (transport_1, protocol_1) = utils.create_test_connection(loop, protocol_factory)

        self.dlm_1 = DataLinkManager(AX25Call("K4DBZ", 1), 1, in_queue_1, out_queue_1, loop.create_future)
        self.transport_1 = cast(utils.TestTransport, transport_1)
        self.protocol_1 = cast(KISSProtocol, protocol_1)

        in_queue_2 = asyncio.Queue()
        out_queue_2 = asyncio.Queue()
        protocol_factory = partial(KISSProtocol, loop, in_queue_2, out_queue_2, port_id=1, check_crc=False)
        (transport_2, protocol_2) = utils.create_test_connection(loop, protocol_factory)

        self.dlm_2 = DataLinkManager(AX25Call("K4DBZ", 2), 2, in_queue_2, out_queue_2, loop.create_future)
        self.transport_2 = cast(utils.TestTransport, transport_2)
        self.protocol_2 = cast(KISSProtocol, protocol_2)

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter('%(levelname)-8s %(asctime)s -- %(message)s'))
        handler.setLevel(logging.DEBUG)

        logger = logging.getLogger("main")
        logger.setLevel(logging.WARNING)
        logger.addHandler(handler)

        state_logger = logging.getLogger("ax25.state")
        state_logger.setLevel(logging.DEBUG)
        state_logger.addHandler(handler)

    def tearDown(self):
        pass

    async def testConnectRetry(self):
        asyncio.create_task(self.dlm_1.start())
        asyncio.create_task(self.dlm_2.start())

        left = asyncio.Queue()
        right = asyncio.Queue()
        self.transport_1.start(left, right)
        self.transport_2.start(right, left)

        self.transport_1.pause_reading()
        assert not await self.dlm_2.dl_connect_request(AX25Call("K4DBZ", 1))

    async def testWindowSize(self):
        class CaptureHandler(L3Handler):
            def __init__(self):
                L3Handler.__init__(self)
                self.captured = []

            def can_handle(self, protocol: L3Protocol) -> bool:
                return protocol == L3Protocol.NoLayer3

            def handle(self, port: int, remote_call: AX25Call, data: bytes) -> bool:
                self.captured.append(data)
                return False

        capture = CaptureHandler()
        self.dlm_2.add_l3_handler(capture)
        asyncio.create_task(self.dlm_1.start())
        asyncio.create_task(self.dlm_2.start())

        left = asyncio.Queue()
        right = asyncio.Queue()
        self.transport_1.start(left, right)
        self.transport_2.start(right, left)

        await self.dlm_2.dl_connect_request(AX25Call("K4DBZ", 1))

        for i in range(100):
            await self.dlm_1.dl_data_request(AX25Call("K4DBZ", 2), L3Protocol.NoLayer3, f"Message {i:03}".encode("ASCII"))

        # Wait for any remaining in-flight packets to get acked
        await asyncio.sleep(3)

        assert len(capture.captured) == 100
        in_order = capture.captured.copy()
        sorted(in_order)
        print(capture.captured)
        assert capture.captured == in_order
