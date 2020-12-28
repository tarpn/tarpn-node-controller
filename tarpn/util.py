import asyncio
import time
from typing import Callable, Optional


class Timer:
    def __init__(self, delay: float, cb: Callable[[], None]):
        """
        A resettable and cancelable timer class
        :param delay: Delay in milliseconds
        :param cb: A callback that takes no arguments
        """
        self.delay = delay
        self._cb = cb
        self._started = 0
        self._timer: Optional[asyncio.Task] = None

    def __repr__(self):
        return f"Timer(delay={self.delay}, remaining={self.remaining()})"

    def start(self):
        if self._timer:
            self._timer.cancel()
        self._started = time.time()
        self._timer = asyncio.get_event_loop().call_later(self.delay / 1000., self._run_cb)

    def _run_cb(self):
        if self._timer:
            # Callback might restart the timer, so clear the timer object first
            self._timer = None
            self._cb()

    def cancel(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def running(self):
        return self._timer is not None

    def remaining(self):
        if self.running():
            return self.delay - ((time.time() - self._started) * 1000.)
        else:
            return -1


def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    if len(lst) == 0:
        yield []
    else:
        for i in range(0, len(lst), n):
            yield lst[i:i + n]


def between(x, lo, hi, inclusive=True):
    if inclusive:
        return lo <= x <= hi
    else:
        return lo < x < hi


def load_plugins():
    import pkg_resources

    discovered_plugins = {
        entry_point.name: entry_point.load()
        for entry_point
        in pkg_resources.iter_entry_points('tarpn.plugins')
    }

    print(discovered_plugins)


def backoff(start_time, growth_factor, max_time):
    """Infinite iterator of exponentially increasing backoff times"""
    yield start_time
    next_time = start_time
    while True:
        next_time = min(max_time, next_time * growth_factor)
        yield next_time

async def shutdown(loop):
    # Give things a chance to shutdown
    await asyncio.sleep(1)
    pending = [task for task in asyncio.all_tasks() if task is not
               asyncio.tasks.current_task()]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    loop.stop()


def graceful_shutdown():
    asyncio.create_task(shutdown(asyncio.get_running_loop()))
