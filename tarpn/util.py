import asyncio
import datetime
import json
import math
import threading
import time
from typing import Callable, Iterator, Dict


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
        self._timer = None

    def __repr__(self):
        return f"Timer(delay={self.delay}, remaining={self.remaining()})"

    def start(self):
        raise NotImplementedError

    def _run_cb(self):
        if self._timer:
            # Callback might restart the timer, so clear the timer object first
            self._timer = None
            self._cb()

    def cancel(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def reset(self):
        self.cancel()
        self.start()

    def running(self):
        return self._timer is not None

    def remaining(self):
        if self.running():
            return self.delay - ((time.time() - self._started) * 1000.)
        else:
            return -1


class AsyncioTimer(Timer):
    def start(self):
        if self._timer:
            self._timer.cancel()
        self._started = time.time()
        self._timer = asyncio.get_event_loop().call_later(self.delay / 1000., self._run_cb)


class ThreadingTimer(Timer):
    def start(self):
        if self._timer:
            self._timer.cancel()
        self._started = time.time()
        self._timer = threading.Timer(self.delay / 1000., self._run_cb)
        self._timer.start()


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


class BackoffGenerator(Iterator):
    def __init__(self, initial_wait: float, growth_factor: float, max_wait: float):
        self.initial_wait = initial_wait
        self.growth_factor = growth_factor
        self.max_wait = max_wait
        self.next_wait = initial_wait
        self._total_waited = 0.0

    def __next__(self):
        this_wait = self.next_wait
        self.next_wait = min(self.max_wait, self.next_wait * self.growth_factor)
        self._total_waited += this_wait
        return this_wait

    def total(self):
        return self._total_waited

    def reset(self):
        self.next_wait = self.initial_wait
        self._total_waited = 0.0


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


class CountDownLatch(object):
    def __init__(self, count=1):
        self.count = count
        self.lock = threading.Condition()

    def countdown(self):
        self.lock.acquire()
        self.count -= 1
        if self.count <= 0:
            self.lock.notifyAll()
        self.lock.release()

    def join(self):
        self.lock.acquire()
        while self.count > 0:
            self.lock.wait()
        self.lock.release()


def json_datetime_default(obj):
    if isinstance(obj, datetime.datetime):
        return {"$datetime": obj.isoformat()}
    raise TypeError ("Type %s not serializable" % type(obj))


def json_datetime_object_hook(obj):
    dt = obj.get('$datetime')
    if dt is not None:
        return datetime.datetime.fromisoformat(dt)
    return obj


def json_dump(filename: str, o: Dict):
    with open(filename, 'w') as fp:
        json.dump(o, fp, indent=2, sort_keys=True, default=json_datetime_default)


def json_load(filename) -> Dict:
    with open(filename, 'r') as fp:
        return json.load(fp, object_hook=json_datetime_object_hook)
