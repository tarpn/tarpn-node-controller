import asyncio
import time
from typing import Callable, Awaitable


class Timer:
    def __init__(self, delay: int, cb: Callable[[], Awaitable[None]]):
        """
        A resettable and cancelable timer class
        :param delay: Delay in seconds
        :param cb: A callback that takes no arguments
        """
        self.delay = delay
        self._cb = cb
        self._task = None
        self._started = 0

    def start(self):
        if self._task:
            self._task.cancel()
        self._task = asyncio.ensure_future(self._run())

    async def _run(self):
        self._started = time.time()
        await asyncio.sleep(self.delay)
        await self._cb()
        self._task = None

    def cancel(self):
        if self._task:
            self._task.cancel()
            self._task = None

    def running(self):
        return self._task is not None

    def remaining(self):
        if self._task:
            return self.delay - (time.time() - self._started)
        else:
            return -1



