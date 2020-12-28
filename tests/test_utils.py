import functools
import unittest

import asyncio

from tarpn.util import Timer
from tests.utils import MockTime


class UtilsTest(unittest.IsolatedAsyncioTestCase):
    async def test_timer(self):
        loop = asyncio.get_event_loop()
        time = MockTime(loop, 0)

        event = asyncio.Event()

        timer = Timer(3000, functools.partial(event.set))
        timer.start()

        time.sleep(4)
        assert event.is_set()
