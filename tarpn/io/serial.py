import logging
import threading
from abc import ABC
from collections import deque
from functools import partial
from time import sleep

import serial

from tarpn.io import IOProtocol
from tarpn.log import LoggingMixin
from tarpn.scheduler import Scheduler, CloseableThreadLoop
from tarpn.util import CountDownLatch, BackoffGenerator


class SerialLoop(CloseableThreadLoop, ABC):
    def __init__(self, name: str, ser: serial.Serial, protocol: IOProtocol,
                 open_event: threading.Event, error_event: threading.Event, closed_latch: CountDownLatch):
        super().__init__(name=name)
        self.ser = ser
        self.protocol = protocol
        self.open_event = open_event
        self.error_event = error_event
        self.closed_latch = closed_latch

    def close(self):
        super().close()
        self.closed_latch.countdown()


class SerialReadLoop(SerialLoop, LoggingMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        LoggingMixin.__init__(self,
                              logger=logging.getLogger("serial"),
                              extra_func=partial(str, f"[Serial Reader {self.ser.name}]"))

    def iter_loop(self):
        if self.open_event.wait(3.0):
            try:
                data = self.ser.read(1024)
                if len(data) > 0:
                    self.debug(f"Read {len(data)} bytes: {data}")
                    self.protocol.handle_bytes(data)
            except serial.SerialException:
                self.exception("Failed to read bytes from serial device")
                self.error_event.set()


class SerialWriteLoop(SerialLoop, LoggingMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        LoggingMixin.__init__(self,
                              logger=logging.getLogger("serial"),
                              extra_func=partial(str, f"[Serial Writer {self.ser.name}]"))
        self.unsent = deque(maxlen=20)
        self.retry_backoff = BackoffGenerator(0.100, 1.2, 1.000)

    def iter_loop(self):
        if self.open_event.wait(0.100):
            if len(self.unsent) > 0:
                to_write = self.unsent.popleft()
            else:
                to_write = self.protocol.next_bytes_to_write()
            if to_write is not None and len(to_write) > 0:
                try:
                    self.ser.write(to_write)
                    self.ser.flush()
                    self.debug(f"Wrote {len(to_write)} bytes: {to_write}")
                    self.retry_backoff.reset()
                except serial.SerialTimeoutException as e:
                    self.unsent.append(to_write)
                    t = next(self.retry_backoff)
                    self.exception(f"Failed to write bytes to serial device, serial timed out. Retrying after {t}s.", e)
                    sleep(t)
                except serial.SerialException as e:
                    self.exception("Failed to write bytes to serial device", e)
                    self.error_event.set()


class SerialDevice(CloseableThreadLoop, LoggingMixin):
    def __init__(self,
                 protocol: IOProtocol,
                 device_name: str,
                 speed: int,
                 timeout: float,
                 scheduler: Scheduler):
        LoggingMixin.__init__(self)
        CloseableThreadLoop.__init__(self, f"Serial Device {device_name}")

        self._scheduler = scheduler
        self._device_name = device_name
        self._protocol = protocol
        self._ser = serial.Serial(port=None, baudrate=speed, timeout=timeout, write_timeout=timeout)
        self._ser.port = device_name
        self._closed_latch = CountDownLatch(2)
        self._open_event = threading.Event()
        self._error_event = threading.Event()
        self._open_backoff = BackoffGenerator(0.100, 1.2, 5.000)
        # Submit the reader and writer threads first, so they will be shutdown first
        self._scheduler.submit(SerialReadLoop(f"Serial Reader {self._ser.name}", self._ser,
                                              self._protocol, self._open_event, self._error_event, self._closed_latch))
        self._scheduler.submit(SerialWriteLoop(f"Serial Writer {self._ser.name}", self._ser,
                                               self._protocol, self._open_event, self._error_event, self._closed_latch))
        self._scheduler.submit(self)

    def close(self) -> None:
        # Stop this loop from re-opening the port
        super().close()

        # Signal to reader and writer that the port is closed
        self._open_event.clear()

        # Wait for them to finish
        self._closed_latch.join()

        # Close the port
        self._ser.close()

    def iter_loop(self):
        """Try to keep the serial port alive"""
        if self._error_event.is_set():
            self.warning("Had a serial error, attempting to reconnect")
            self._open_event.clear()
            self._error_event.clear()
            self._ser.close()
            sleep(next(self._open_backoff))
            return

        if not self._ser.is_open:
            self.info(f"Opening serial port {self._device_name}")
            try:
                self._ser.open()
                self.info(f"Opened serial port {self._device_name}")
                self._open_event.set()
                self._open_backoff.reset()
            except serial.SerialException as err:
                t = next(self._open_backoff)
                self.warning(f"Failed to open serial port {self._device_name} with {err}, trying again in {t:0.3f}s")
                sleep(t)
        else:
            sleep(1)
