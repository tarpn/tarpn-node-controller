import asyncio
import time
from typing import Callable, Optional


class Timer:
    def __init__(self, delay: float, cb: Callable[[], None]):
        """
        A resettable and cancelable timer class
        :param delay: Delay in seconds
        :param cb: A callback that takes no arguments
        """
        self.delay = delay
        self._cb = cb
        self._started = 0
        self._timer: Optional[asyncio.TimerHandle] = None

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


def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    if len(lst) == 0:
        yield []
    else:
        for i in range(0, len(lst), n):
            yield lst[i:i + n]


def load_plugins():
    import pkg_resources

    discovered_plugins = {
        entry_point.name: entry_point.load()
        for entry_point
        in pkg_resources.iter_entry_points('tarpn.plugins')
    }

    print(discovered_plugins)