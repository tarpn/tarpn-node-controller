import asyncio
import logging
import sys
import time
import unittest
from functools import partial
from typing import cast

from tarpn.ax25 import AX25Call, AX25StateType, L3Protocol
from tarpn.ax25.datalink import DataLinkManager
from tarpn.events import EventBus, EventListener
from tarpn.netrom.network import NetworkManager
from tarpn.port.kiss import KISSProtocol
from tarpn.settings import NetworkConfig
from tests import utils


class TestL4(unittest.IsolatedAsyncioTestCase):
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
        self.nl_1 = NetworkManager(NetworkConfig.from_dict({
            "netrom.node.call": "K4DBZ-1",
            "netrom.node.alias": "NODE1",
            "netrom.nodes.interval": 10
        }), loop)
        self.nl_1.attach_data_link(self.dlm_1)

        self.transport_1 = cast(utils.TestTransport, transport_1)
        self.protocol_1 = cast(KISSProtocol, protocol_1)

        in_queue_2 = asyncio.Queue()
        out_queue_2 = asyncio.Queue()
        protocol_factory = partial(KISSProtocol, loop, in_queue_2, out_queue_2, port_id=2, check_crc=False)
        (transport_2, protocol_2) = utils.create_test_connection(loop, protocol_factory)

        self.dlm_2 = DataLinkManager(AX25Call("K4DBZ", 2), 2, in_queue_2, out_queue_2, loop.create_future)
        self.nl_2 = NetworkManager(NetworkConfig.from_dict({
            "netrom.node.call": "K4DBZ-2",
            "netrom.node.alias": "NODE2",
            "netrom.nodes.interval": 10
        }), loop)
        self.nl_2.attach_data_link(self.dlm_2)

        self.transport_2 = cast(utils.TestTransport, transport_2)
        self.protocol_2 = cast(KISSProtocol, protocol_2)

        logger = logging.getLogger("main")
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter('%(levelname)-8s %(asctime)s -- %(message)s'))
        handler.setLevel(logging.INFO)
        logger.addHandler(handler)

    def tearDown(self):
        pass

    async def testConnectAndDisconnect(self):
        asyncio.create_task(self.dlm_1.start())
        asyncio.create_task(self.nl_1.start())

        asyncio.create_task(self.dlm_2.start())
        asyncio.create_task(self.nl_2.start())

        left = asyncio.Queue()
        right = asyncio.Queue()
        self.transport_1.start(left, right)
        self.transport_2.start(right, left)

        self.nl_2.bind(AX25Call("K4DBZ", 2), "NODE2")

        def _on_data(my_circuit_id: int, remote_call: AX25Call, data: bytes, *args, **kwargs):
            print(f"_on_data: {data}")

        EventBus.bind(EventListener(
            f"netrom.K4DBZ-2.inbound",
            f"netrom_K4DBZ-2_inbound",
            _on_data
        ))

        await self.dlm_2.dl_connect_request(AX25Call("K4DBZ", 1))

        await asyncio.create_task(self.nl_1._broadcast_nodes())
        await asyncio.create_task(self.nl_2._broadcast_nodes())

        # TODO wait for both ends to learn about each other
        await asyncio.sleep(1)

        circuit = await self.nl_1.nl_connect_request(AX25Call("K4DBZ", 2), AX25Call("K4DBZ", 1))
        await self.nl_1.nl_connect_request(AX25Call("K4DBZ", 2), AX25Call("K4DBZ", 1))

        for i in range(10):
            await self.nl_1.nl_data_request(circuit, AX25Call("K4DBZ", 2), AX25Call("K4DBZ", 1), f"Test {i}".encode("utf-8"))

        await self.nl_1.nl_disconnect_request(circuit, AX25Call("K4DBZ", 2), AX25Call("K4DBZ", 1))
