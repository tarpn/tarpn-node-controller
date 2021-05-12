import asyncio
import datetime
from asyncio import Protocol, BaseProtocol, Transport
from typing import Any, Callable, Optional, Dict, Iterator

import tarpn.ax25
from tarpn.datalink import L2Payload, L2Address
from tarpn.datalink.protocol import LinkMultiplexer, L2Protocol
from tarpn.log import LoggingMixin
from tarpn.network import L3Payload, L3Protocol
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
        self.neighbors: Dict[int, L3Protocol] = {}

    def attach_neighbor(self, link_id: int, protocol: L3Protocol):
        self.neighbors[link_id] = protocol

    def offer(self, payload: L3Payload) -> bool:
        neighbor = self.neighbors.get(payload.link_id)
        if neighbor is not None:
            l2 = L2Payload(
                link_id=payload.link_id,
                source=L2Address(),
                destination=L2Address(),
                l3_protocol=tarpn.ax25.L3Protocol(payload.protocol),
                l3_data=payload.buffer
            )
            neighbor.handle_l2_payload(l2)
            return True
        else:
            return False

    def mtu(self) -> int:
        return 100

    def links_for_address(self,
                          l2_address: L2Address,
                          exclude_device_with_link_id: Optional[int] = None) -> Iterator[int]:
        for link_id in self.neighbors.keys():
            if link_id == exclude_device_with_link_id:
                continue
            yield link_id

    def register_device(self, l2_protocol: L2Protocol) -> None:
        pass

    def add_link(self, l2_protocol: L2Protocol) -> int:
        return -1

    def remove_link(self, link_id: int) -> None:
        pass

    def get_link(self, link_id: int) -> Optional[L2Protocol]:
        return None
