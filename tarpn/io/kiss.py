import logging
from typing import Optional

from tarpn.io import IOProtocol
from tarpn.datalink import L2IOQueuing, FrameData
from tarpn.log import LoggingMixin
from tarpn.metrics import MetricsMixin
from tarpn.port.kiss import KISSMagic, decode_kiss_frame, KISSCommand, KISSFrame, encode_kiss_frame


class KISSProtocol(IOProtocol, LoggingMixin, MetricsMixin):
    """
    One thread to read off the serial device into a buffer, do streaming decoding, and write
    decoded frames into an inbound LIFO queue. If the queue is full, we drop new frames.
    """

    def __init__(self,
                 port_id: int,
                 queue: L2IOQueuing,
                 check_crc: bool = False,
                 hdlc_port: int = 0):
        self.port_id = port_id
        self.check_crc = check_crc
        self.hdlc_port = hdlc_port

        self.msgs_recvd = 0
        self.in_frame = False
        self.saw_fend = False

        self._l2_queue: L2IOQueuing = queue
        self._send_immediate: Optional[bytes] = None
        self._buffer = bytearray()

        self.closed = False
        LoggingMixin.__init__(self, logging.getLogger("main"))

    def handle_bytes(self, data: bytes) -> None:
        """
        Collect data until a whole frame has been read (FEND to FEND) and then emit a Frame
        """
        for b in data:
            if b == KISSMagic.FEND:
                if self.in_frame:
                    frame = decode_kiss_frame(self._buffer, self.check_crc)
                    if frame is not None and self.saw_fend:
                        self.debug(f"Received frame {frame}")
                        if frame.command == KISSCommand.Data:
                            self.meter("kiss", "decoded").mark()
                            self._l2_queue.offer_inbound(FrameData(self.port_id, frame.data))
                        elif frame.command == KISSCommand.SetHardware:
                            self.counter("kiss", "hardware").inc()
                            self._on_hardware(frame)
                        else:
                            self.warning(f"Dropping KISS frame with unsupported command {frame.command}: {frame}")
                            self.counter("kiss", "unknown").inc()
                        self.msgs_recvd += 1
                    elif not self.saw_fend:
                        self.warning(f"Dropping partial KISS frame {frame}")
                        self.counter("kiss", "partial").inc()
                    else:
                        self.warning(f"Could not decode frame from bytes {self._buffer}, dropping")
                        self.counter("kiss", "error").inc()
                    self._buffer.clear()
                    self.saw_fend = False
                    self.in_frame = False
                else:
                    # keep consuming sequential FENDs
                    self.saw_fend = True
                    continue
            else:
                if not self.in_frame:
                    self.in_frame = True
                self._buffer.append(b)

    def next_bytes_to_write(self) -> bytes:
        """
        Take the next available frame, KISS encode it, and return it for immediate writing
        """
        if self._send_immediate is not None:
            ret = self._send_immediate
            self._send_immediate = None
            return ret

        ready_frame = self._l2_queue.take_outbound()
        if ready_frame is not None:
            kiss_frame = KISSFrame(self.hdlc_port, KISSCommand.Data, ready_frame.data)
            kiss_data = encode_kiss_frame(kiss_frame, False)
            self.meter("kiss", "encoded").mark()
            return kiss_data
        else:
            return bytes()

    def _on_hardware(self, frame: KISSFrame):
        """
        Callback for when we receive a SetHardware query.

        TODO This is handled out of band from regular data frames and bypasses the queue
        """
        if frame.command != KISSCommand.SetHardware:
            return
        hw_req = frame.data.decode("ascii").strip()
        if hw_req == "TNC:":
            hw_resp = "TNC:tarpn 0.1"
        elif hw_req == "MODEM:":
            hw_resp = "MODEM:tarpn"
        elif hw_req == "MODEML:":
            hw_resp = "MODEML:tarpn"
        else:
            # TODO what hardware strings are expected?
            hw_resp = ""

        kiss_command = KISSFrame(frame.hdlc_port, KISSCommand.SetHardware, hw_resp.encode("ascii"))
        kiss_data = encode_kiss_frame(kiss_command, False)
        self._send_immediate = kiss_data
