from functools import partial
from typing import NamedTuple, Callable
from enum import IntEnum, unique
import logging

import asyncio

import serial_asyncio

from tarpn.port import PortFrame, logger
from tarpn.settings import PortConfig

logger = logging.getLogger("kiss")

@unique
class KISSMagic(IntEnum):
    FEND = 0xC0
    FESC = 0xDB
    TFEND = 0xDC
    TFESC = 0xDD


@unique
class KISSCommand(IntEnum):
    Unknown = 0xFE
    Data = 0x00
    TxDelay = 0x01
    P = 0x02
    SlotTime = 0x03
    TxTail = 0x04
    FullDuplex = 0x05
    SetHardware = 0x06
    Return = 0xFF

    @staticmethod
    def from_int(i):
        for name, member in KISSCommand.__members__.items():
            if member.value == i:
                return member
        return KISSCommand.Unknown


class KISSFrame(NamedTuple):
    hdlc_port: int
    command: KISSCommand
    data: bytes


class KISSProtocol(asyncio.Protocol):
    """
    An asyncio Protocol that parses an incoming data stream as KISS frames. When handling data from
    the Transport, it does not need to include an entire frame. This protocol does streaming parsing
    of the incoming bytes.

    Once a full valid KISS frame is detected, it is decoded and written to the given outbound queue
    for further processing.

    This class also has an run loop which reads frames off of the inbound queue and encapsulates them
    as KISS payloads before sending to the transport for I/O.
    """
    def __init__(self,
                 loop: asyncio.AbstractEventLoop,
                 inbound: asyncio.Queue,    # DataLinkFrame
                 outbound: asyncio.Queue,   # DataLinkFrame
                 port_id: int,
                 check_crc=False):
        self.loop = loop
        self.inbound = inbound
        self.outbound = outbound
        self.check_crc = check_crc
        self.port_id = port_id
        self.transport = None
        self.msgs_recvd = 0
        self.in_frame = False
        self._buffer = bytearray()
        self._stopped = False

    def connection_made(self, transport):
        logger.info(f"Opened connection on {transport}")
        self.transport = transport
        self.loop.create_task(self.start())

    def data_received(self, data):
        """Collect data until a whole frame has been read (FEND to FEND) and then emit a Frame
        """
        for b in data:
            if b == KISSMagic.FEND:
                if self.in_frame:
                    frame = decode_kiss_frame(self._buffer, self.check_crc)
                    logger.debug(f"Received {frame}")
                    if frame.command == KISSCommand.Data:
                        asyncio.ensure_future(self.inbound.put(
                            PortFrame(self.port_id, frame.data, frame.hdlc_port,
                                      self._data_callback(frame.hdlc_port))))
                    elif frame.command == KISSCommand.SetHardware:
                        self.on_hardware(frame)
                    else:
                        logger.warning(f"Ignoring KISS frame {frame}")
                    self.msgs_recvd += 1
                    self._buffer.clear()
                    self.in_frame = False
                else:
                    # keep consuming sequential FENDs
                    continue
            else:
                if not self.in_frame:
                    self.in_frame = True
                self._buffer.append(b)

    def _data_callback(self, hdlc_port) -> Callable[[bytes], None]:
        """Callback for sending data out the same way it came. Used in Frame objects
        """
        def inner(data):
            self.write(KISSFrame(hdlc_port, KISSCommand.Data, data))
        return inner

    def write(self, frame: KISSFrame):
        """Accept a KISS frame and enqueue it for writing to the serial transport
        """
        data = encode_kiss_frame(frame, False)
        logger.debug(f"Scheduling {data} for sending")
        asyncio.ensure_future(self.outbound.put(data))

    async def start(self):
        while not self._stopped:
            await self._loop()

    async def _loop(self):
        frame: PortFrame = await self.outbound.get()
        if frame:
            self._loop_sync(frame)
            self.outbound.task_done()

    def _loop_sync(self, frame: PortFrame):
        kiss_frame = KISSFrame(frame.hldc_port, KISSCommand.Data, frame.data)
        kiss_data = encode_kiss_frame(kiss_frame, False)
        self.transport.write(kiss_data)

    def connection_lost(self, exc):
        logger.info(f"Closed connection on {self.transport}")

    def should_check_crc(self):
        return self.check_crc

    def on_hardware(self, frame: KISSFrame):
        """Callback for when we receive a SetHardware query
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

        resp = KISSFrame(frame.hdlc_port, KISSCommand.SetHardware, hw_resp.encode("ascii"))
        self.write(resp)


def encode_kiss_frame(frame: KISSFrame, include_crc=False):
    """Given a KISSFrame, encode into bytes for sending over an asynchronous transport
    """
    crc = 0
    out = bytes([KISSMagic.FEND])
    command_byte = ((frame.hdlc_port << 4) & 0xF0) | (frame.command & 0x0F);
    out += int.to_bytes(command_byte, 1, 'big')
    for b in frame.data:
        crc ^= b
        if b == KISSMagic.FEND:
            out += bytes([KISSMagic.FESC, KISSMagic.TFEND])
        elif b == KISSMagic.FESC:
            out += bytes([KISSMagic.FESC, KISSMagic.TFESC])
        else:
            out += bytes([b])
    if include_crc:
        out += bytes([crc & 0xFF])
    out += bytes([KISSMagic.FEND])
    return out


def decode_kiss_frame(data, check_crc=False):
    """Given a KISS frame (excluding the frame delimiters), decode the port and command.
    Also un-escape the data
    """
    crc = 0
    first_byte = data[0]
    crc ^= first_byte
    hdlc_port = (first_byte >> 4) & 0x0F
    kiss_command = KISSCommand.from_int(first_byte & 0x0F)
    in_escape = False
    decoded = bytes()
    for b in data[1:]:
        if b == KISSMagic.FESC:
            in_escape = True
        else:
            if in_escape:
                if b == KISSMagic.TFEND:
                    decoded += KISSMagic.FEND.to_bytes(1, 'big')
                elif b == KISSMagic.TFESC:
                    decoded += KISSMagic.FESC.to_bytes(1, 'big')
                in_escape = False
            else:
                decoded += b.to_bytes(1, 'big')

    if check_crc:
        kiss_crc = decoded[-1]
        decoded = decoded[:-1]
        for b in decoded:
            crc ^= b
        crc &= 0xFF
        if kiss_crc == crc:
            return KISSFrame(hdlc_port, kiss_command, decoded)
        else:
            logger.warning("CRC failure. Discarding frame")
            return None
    else:
        return KISSFrame(hdlc_port, kiss_command, decoded)


async def kiss_port_factory(in_queue: asyncio.Queue, out_queue: asyncio.Queue, port_config: PortConfig):
    loop = asyncio.get_event_loop()

    if port_config.port_type() == "serial":
        protocol_factory = partial(KISSProtocol, loop, in_queue, out_queue, port_id=port_config.port_id(),
                                   check_crc=port_config.get_boolean("kiss.checksum", False))
        await serial_asyncio.create_serial_connection(
            loop, protocol_factory, port_config.get("serial.device"), baudrate=port_config.get("serial.speed"))
        logger.info(f"Created Serial Port {port_config.port_id()}")
    elif port_config.port_type() == "tcp":
        #  TODO TCP doesn't really work yet
        protocol_factory = partial(KISSProtocol, loop, in_queue, out_queue, port_id=port_config.port_id(),
                                   check_crc=port_config.get_boolean("kiss.checksum", False))
        tcp_server = await loop.create_server(protocol_factory, "127.0.0.1", 8000)
        await tcp_server.start_serving()
        logger.info(f"Created TCP Port {port_config.port_id()}")
    else:
        logger.warning(f"Ignoring unknown port type {port_config.port_type()} for port {port_config.port_id()}")