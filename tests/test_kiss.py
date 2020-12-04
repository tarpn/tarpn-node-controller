import asyncio
import unittest
from functools import partial
from typing import cast

import tests.utils
from tarpn.port import PortFrame
from tarpn.port.kiss import KISSProtocol


class TestKiss(unittest.IsolatedAsyncioTestCase):
    async def test_protocol(self):
        loop = asyncio.get_event_loop()
        in_queue = asyncio.Queue()
        out_queue = asyncio.Queue()
        protocol_factory = partial(KISSProtocol, loop, in_queue, out_queue, port_id=1, check_crc=False)
        (transport, protocol) = tests.utils.create_test_connection(loop, protocol_factory)

        kiss_protocol = cast(KISSProtocol, protocol)
        test_transport = cast(tests.utils.TestTransport, transport)

        kiss_protocol._loop_sync(PortFrame(0, "Testing".encode("ascii")))
        data = test_transport.captured_writes()[0]

        test_transport.read(data)
        incoming = await in_queue.get()
        assert incoming.data == b'Testing'
