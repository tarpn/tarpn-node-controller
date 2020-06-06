import asyncio
import time
from typing import Callable, Awaitable


class Timer:
    def __init__(self, delay: int, cb: Callable[[], None]):
        """
        A resettable and cancelable timer class
        :param delay: Delay in seconds
        :param cb: A callback that takes no arguments
        """
        self.delay = delay
        self._cb = cb
        self._timer: asyncio.TimerHandle or None = None
        self._started = 0

    def start(self):
        if self._timer:
            self._timer.cancel()
        self._timer = asyncio.get_event_loop().call_later(self.delay, self._run_cb)

    def _run_cb(self):
        try:
            self._cb()
        finally:
            self._timer = None

    def cancel(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def running(self):
        return self._timer is not None

    def remaining(self):
        if self.running():
            return self.delay - (time.time() - self._started)
        else:
            return -1



