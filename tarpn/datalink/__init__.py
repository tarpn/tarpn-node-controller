import itertools
import queue
import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple

from tarpn.log import LoggingMixin
from tarpn.metrics import MetricsMixin


@dataclass
class FrameData:
    port: int
    data: bytes


class L2Address:
    pass


@dataclass
class L2Payload:
    link_id: int
    source: L2Address
    destination: L2Address
    l3_protocol: int
    l3_data: bytes


class L2Queuing:
    """
    Used by the L2 layer for reading from and writing to the L2 queues
    """
    def offer_outbound(self, frame: FrameData) -> bool:
        """
        Offer a frame for transmission on the IO device at the next available time. This method
        will return immediately.

        :param frame:   The frame data
        :return:        true if the frame was accepted by the L2 queue, false otherwise
        """
        raise NotImplementedError

    def take_inbound(self) -> Tuple[FrameData, int]:
        """
        Consume incoming frame data from the IO device. This method will block until an inbound
        frame is ready.

        :return:        A tuple of the frame data and the number of inbound frames that have been
                        dropped since the last call to take_inbound due to the queue being full.
        """
        raise NotImplementedError

    def qsize(self) -> int:
        raise NotImplementedError

    def mtu(self) -> int:
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


class L2IOQueuing:
    """
    Used by the IO layer for reading from and writing to the L2 queues
    """
    def take_outbound(self) -> Optional[FrameData]:
        """
        Consume an outbound frame from the L2 queue.

        This method should block until data is available for writing, otherwise
        the writer will be in a tight loop
        """
        raise NotImplementedError

    def offer_inbound(self, frame: FrameData) -> None:
        """
        Offer a frame to the L2 inbound queue. This method must not block and must
        always succeed.
        """
        raise NotImplementedError


class L2FIFOQueue(L2Queuing, L2IOQueuing, MetricsMixin, LoggingMixin):
    """
    Pair of default L2 queues using FIFO queues, one inbound and one outbound.

    Both of these queues are bounded. If the outbound queue is full, calls to
    offer_outbound will return False and the "full" meter will be incremented.
    This typically happens when there is a failure with the I/O device that is
    being written to. For example, a serial device with a full buffer. In these
    cases the desired behavior is to drop outgoing messages.

    If the inbound queue is full, it means the higher layers cannot keep up
    with rate of incoming data. In this case we drop older items from the queue
    so that when the upper layers catch up they have the most recent messages.
    """
    def __init__(self, queue_size, max_payload_size):
        LoggingMixin.__init__(self)
        self._max_payload_size = max_payload_size
        self._outbound = queue.Queue(maxsize=queue_size)

        # Ring buffer for decoded inbound messages
        self._inbound = deque(maxlen=queue_size)
        self._inbound_count = itertools.count()
        self._last_inbound = next(self._inbound_count)

        self._lock: threading.Lock = threading.Lock()
        self._not_empty: threading.Condition = threading.Condition(self._lock)

    def qsize(self) -> int:
        return self._outbound.maxsize

    def mtu(self) -> int:
        return self._max_payload_size

    def offer_outbound(self, frame: FrameData) -> bool:
        if len(frame.data) > self._max_payload_size:
            self.error(f"Payload too large, dropping. Size is {len(frame.data)}, max is {self._max_payload_size}")
            # TODO indicate an error here
            self.counter("outbound", "mtu.error").inc()
            return False
        else:
            try:
                self.meter("outbound", "offer").mark()
                frame.timer = self.timer("outbound", "wait").time()
                self._outbound.put(frame, False, None)
                return True
            except queue.Full:
                self.warning(f"Outbound queue is full.")
                self.counter("outbound", "full").inc()
                return False

    def take_outbound(self) -> Optional[FrameData]:
        try:
            frame = self._outbound.get(True, 0.200)
            self.meter("outbound", "take").mark()
            frame.timer.stop()
            return frame
        except queue.Empty:
            return None

    def offer_inbound(self, frame: FrameData) -> None:
        # Since the deque is bounded, this will eject old items to make room for this frame
        with self._lock:
            self.meter("inbound", "offer").mark()
            #frame.timer = self.timer("inbound", "wait").time()
            self._inbound.append((frame, next(self._inbound_count)))
            self._not_empty.notify()

    def take_inbound(self) -> Tuple[FrameData, int]:
        with self._not_empty:
            while not len(self._inbound):
                self._not_empty.wait()
            inbound, count = self._inbound.popleft()
            #inbound.timer.stop()
            dropped = count - self._last_inbound - 1
            self._last_inbound = count
            self.meter("inbound", "take").mark()
            if dropped > 0:
                self.meter("inbound", "dropped").mark(dropped)
            return inbound, dropped

    def close(self):
        with self._lock:
            self.meter("inbound", "offer").mark()
            self._inbound.append((None, -1))
            self._not_empty.notify()