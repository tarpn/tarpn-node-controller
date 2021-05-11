import asyncio
import datetime
import itertools
from asyncio import Protocol, BaseProtocol, Transport, AbstractEventLoop
from typing import Any, Callable, Optional, Dict

from tarpn.datalink.protocol import LinkMultiplexer, L2Protocol
from tarpn.log import LoggingMixin
from tarpn.network import L3Queueing, L3PriorityQueue
from tarpn.scheduler import Scheduler, CloseableThread
from tarpn.util import Timer, Time


class TestTransport(Transport):
    def __init__(self, loop, protocol):
        super().__init__()
        self._loop = loop
        self._protocol: Protocol = protocol
        self._closed = False
        self._paused = False

    def start(self, inbound: asyncio.Queue, outbound: asyncio.Queue):
        self._outbound = outbound
        asyncio.create_task(self._run(inbound))

    async def _run(self, inbound: asyncio.Queue):
        while not self._closed:
            if self._paused:
                await asyncio.sleep(0.001)
                continue
            item = await inbound.get()
            if item:
                self._protocol.data_received(item)
                inbound.task_done()

    def is_reading(self) -> bool:
        return not self._closed

    def get_protocol(self) -> BaseProtocol:
        return self._protocol

    def write(self, data: Any) -> None:
        asyncio.create_task(self._outbound.put(data))

    def close(self):
        self._protocol.eof_received()
        self._closed = True

    def pause_reading(self) -> None:
        self._paused = True

    def resume_reading(self) -> None:
        self._paused = True


def create_test_connection(loop, protocol_factory, *args, **kwargs):
    protocol = protocol_factory()
    transport = TestTransport(loop, protocol)
    protocol.connection_made(transport)
    return (transport, protocol)


class MockTime(Time):
    def __init__(self, initial_time: int = 0):
        self._time = initial_time

    def time(self) -> float:
        return self._time

    def datetime(self) -> datetime.datetime:
        return datetime.datetime.fromtimestamp(self.time())

    def sleep(self, sec):
        self._time += sec

    def tick(self):
        self._time += 1


class MockTimer(Timer):
    def __init__(self, scheduler: Scheduler, delay: float, cb: Callable[[], None]):
        super().__init__(delay, cb)
        self.scheduler = scheduler

    def start(self):
        pass

    def cancel(self):
        pass

    def reset(self):
        pass

    def running(self):
        return False

    def remaining(self):
        return 0


class MockScheduler(Scheduler):
    def __init__(self, time: MockTime):
        LoggingMixin.__init__(self)
        self.time = time
        self.tasks = []
        self.shutdown_tasks = []

    def timer(self, delay: float, cb: Callable[[], None], auto_start=False) -> Timer:
        t = MockTimer(self, delay, cb)
        if auto_start:
            t.start()
        return t

    def submit(self, thread: CloseableThread):
        thread.run()

    def run(self, runnable: Callable[..., Any]):
        runnable()

    def add_shutdown_hook(self, runnable: Callable[..., Any]):
        self.shutdown_tasks.append(runnable)

    def join(self):
        return

    def shutdown(self):
        for task in self.shutdown_tasks:
            task()


class MockLinkMultiplexer(LinkMultiplexer):
    def __init__(self):
        self.devices: Dict[int, L2Protocol] = {}
        self.queues: Dict[int, L3Queueing] = {}
        self.links: Dict[int, L2Protocol] = {}
        self.link_id_counter = itertools.count()

    def get_queue(self, link_id: int) -> Optional[L3Queueing]:
        l2 = self.links.get(link_id)
        if l2 is not None:
            return self.queues.get(l2.get_device_id())
        else:
            return None

    def register_device(self, l2_protocol: L2Protocol) -> None:
        self.devices[l2_protocol.get_device_id()] = l2_protocol
        self.queues[l2_protocol.get_device_id()] = L3PriorityQueue()

    def get_registered_devices(self) -> Dict[int, L2Protocol]:
        return dict(self.devices)

    def add_link(self, l2_protocol: L2Protocol) -> int:
        link_id = next(self.link_id_counter)
        self.links[link_id] = l2_protocol
        return link_id

    def remove_link(self, link_id: int) -> None:
        del self.links[link_id]

    def get_link(self, link_id: int) -> Optional[L2Protocol]:
        return self.links.get(link_id)

    def poll(self, link_id: int):
        queue = self.get_queue(link_id)
        l3_payload = queue.maybe_take()
        if l3_payload is not None:
            self.get_link(link_id).send_packet(l3_payload)

