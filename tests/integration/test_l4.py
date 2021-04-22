import logging
import sys
import unittest
from functools import partial
from typing import cast

from tarpn.ax25 import AX25Call
from tarpn.ax25.datalink import DataLinkManager
from tarpn.netrom.network import NetworkManager
from tarpn.port.kiss import KISSProtocol
from tarpn.settings import NetworkConfig
from tests import utils


class TestL4(unittest.TestCase):
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
