import asyncio
import datetime
import json
import secrets
import struct
import threading
import time
from io import BytesIO
from itertools import cycle
from typing import Callable, Iterator, Dict, Collection, List, Set, Tuple


class Timer:
    def __init__(self, delay_ms: float, cb: Callable[[], None]):
        """
        A resettable and cancelable timer class
        :param delay_ms: Delay in milliseconds
        :param cb: A callback that takes no arguments
        """
        self.delay = delay_ms
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


class VectorClock(Collection[int]):
    def __init__(self, *args):
        timestamps = []
        for arg in args:
            if isinstance(arg, int):
                timestamps.append(arg)
            else:
                raise ValueError("Expected list of int values")
        self._timestamps: List[int] = timestamps

    def to_int_list(self):
        return self._timestamps.copy()

    def copy(self):
        return self.__copy__()

    def mutable(self):
        return MutableVectorClock(*self._timestamps)

    def __copy__(self):
        return VectorClock(*self._timestamps)

    def __repr__(self):
        return "VectorClock(" + ", ".join(str(t) for t in self._timestamps) + ")"

    def __len__(self) -> int:
        return len(self._timestamps)

    def __getitem__(self, item):
        return self._timestamps.__getitem__(item)

    def __iter__(self) -> Iterator[int]:
        return self._timestamps.__iter__()

    def __contains__(self, item):
        return self._timestamps.__contains__(item)

    def _assert_comparable(self, other):
        if not isinstance(other, VectorClock):
            raise TypeError(f"Cannot compare {other.__class__} with VectorClock")
        if len(other._timestamps) != len(self._timestamps):
            raise TypeError(f"Cannot compare {other.__class__} with VectorClock")

    def __ne__(self, other):
        return not self.__eq__(other)

    def __eq__(self, other):
        self._assert_comparable(other)
        for i in range(len(other)):
            if other[i] != self._timestamps[i]:
                return False
        return True

    def __le__(self, other):
        self._assert_comparable(other)
        for i in range(len(other)):
            if self._timestamps[i] > other[i]:
                return False
        return True

    def __lt__(self, other):
        self._assert_comparable(other)
        return self <= other and self != other

    def __or__(self, other):
        return (not self <= other) and (not other <= self)


class MutableVectorClock(VectorClock):
    def __setitem__(self, key, value):
        self._timestamps[key] = value

    def __copy__(self):
        return MutableVectorClock(*self._timestamps)

    def inc_timestamp(self, idx):
        self[idx] += 1
        return self

    def set_timestamp(self, idx, timestamp):
        if self[idx] <= timestamp:
            self[idx] = timestamp
            return self
        else:
            raise ValueError(f"Timestamps must monotonically increase. "
                             f"{timestamp} is less than current timestamp {self[idx]}")


class ByteUtils:
    @staticmethod
    def write_utf8(data: BytesIO, string: str) -> None:
        utf8_bytes = string.encode("utf-8")
        utf8_len = len(utf8_bytes)
        data.write(struct.pack(">B", utf8_len))
        data.write(utf8_bytes)

    @staticmethod
    def read_utf8(data: BytesIO) -> str:
        utf8_len = struct.unpack(">B", data.read(1))[0]
        utf8_bytes = data.read(utf8_len)
        string = utf8_bytes.decode("utf-8")
        return string

    @staticmethod
    def read_int8(data: BytesIO) -> int:
        return struct.unpack(">b", data.read(1))[0]

    @staticmethod
    def write_int8(data: BytesIO, val: int) -> None:
        data.write(struct.pack(">b", val))

    @staticmethod
    def read_uint8(data: BytesIO) -> int:
        return struct.unpack(">B", data.read(1))[0]

    @staticmethod
    def write_uint8(data: BytesIO, val: int) -> None:
        data.write(struct.pack(">B", val))

    @staticmethod
    def read_int16(data: BytesIO) -> int:
        return struct.unpack(">h", data.read(2))[0]

    @staticmethod
    def write_int16(data: BytesIO, val: int) -> None:
        data.write(struct.pack(">h", val))

    @staticmethod
    def read_uint16(data: BytesIO) -> int:
        return struct.unpack(">H", data.read(2))[0]

    @staticmethod
    def write_uint16(data: BytesIO, val: int) -> None:
        data.write(struct.pack(">H", val))

    @staticmethod
    def write_hi_lo(data: BytesIO, hi: int, lo: int, hi_n: int):
        assert hi == hi & (2 ** hi_n - 1)
        assert lo == lo & (2 ** (8 - hi_n) - 1)
        byte = (hi << (8 - hi_n)) | lo
        data.write(struct.pack(">B", byte))

    @staticmethod
    def hi_bits(byte: int, n: int):
        return ((byte & 0xFF) >> (8 - n)) & (2**n-1)

    @staticmethod
    def lo_bits(byte: int, n: int):
        return byte & (2**n-1)


class Time:
    def time(self) -> int:
        raise NotImplementedError()

    def datetime(self) -> datetime.datetime:
        raise NotImplementedError()


class WallTime(Time):
    def time(self) -> float:
        return time.time() * 1000.

    def datetime(self) -> datetime:
        return datetime.datetime.utcnow()


class Sequence(int):
    def __lt__(self, other):
        delta = super().__sub__(other)
        signed = struct.unpack('<h', struct.pack('<H', delta))[0]
        if signed < 0:
            return -1
        elif signed > 0:
            return 1
        else:
            return 0

    def __sub__(self, other):
        delta = abs(super().__sub__(other))
        signed = struct.unpack('<h', struct.pack('<H', delta))[0]
        return signed


class TTLCache:
    def __init__(self, time: Time, expiry_ms: int):
        self.time = time
        self.expiry_ms = expiry_ms
        self.cache: Set[int] = set()
        self.seen: List[Tuple[int, int]] = list()

    def contains(self, hash_code: int) -> bool:
        """Check if a packet header has been seen before"""
        now = self.time.time()
        removed = []
        for i, (expire, item) in zip(range(len(self.seen)), self.seen):
            if now > expire:
                removed.append(i)
                self.cache.remove(item)
        for i in removed[::-1]:
            del self.seen[i]

        if hash_code in self.cache:
            return True
        else:
            self.cache.add(hash_code)
            self.seen.append((now + self.expiry_ms, hash_code))
            return False


def lollipop_sequence():
    "Returns a generator of an 8-bit lollipop sequence"
    for i in range(-128, 128):
        yield i
    for i in cycle(range(0, 128)):
        yield i


def lollipop_compare(old_epoch: int, new_epoch: int) -> int:
    """
    Compares two 8-bit lollipop sequences
    :returns: a value indicating if the given new_epoch is in fact newer than old_epoch
        1 if the new_epoch is newer than old_epoch
        0 if the new_epoch is newer and the sequence has been reset
        -1 if new_epoch is not newer than old_epoch
    """
    if old_epoch == new_epoch:
        return -1

    if new_epoch > old_epoch:
        # Case 1: new epoch is greater, 43 > 42
        return 1
    elif new_epoch < 0 <= old_epoch:
        # Case 2: new epoch is lesser, but is negative, -127 < 0 <= 10
        return 0
    elif new_epoch < old_epoch < 0:
        # Case 3: both negative, this is either a reset or a delayed packet
        # from another neighbor. Let's assume it's a reset. -126 < -120 < 0
        return 0
    elif new_epoch < old_epoch and (old_epoch - new_epoch > 32):
        # Case 3: wrap around, 10 < 128, (128 - 10 > 32)
        return 1
    else:
        return -1


def secure_random_byte() -> int:
    return struct.unpack('<B', secrets.token_bytes(1))[0]


def secure_random_data(size: int) -> bytes:
    return secrets.token_bytes(size)