from typing import NamedTuple, Callable
from enum import IntEnum, unique
import logging

import asyncio

from tarpn.frame import DataLinkFrame
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
    def __init__(self,
                 loop: asyncio.AbstractEventLoop,
                 inbound: asyncio.Queue,    # DataLinkFrame
                 outbound: asyncio.Queue,   # DataLinkFrame
                 port_config: PortConfig,
                 check_crc=False):
        self.loop = loop
        self.inbound = inbound
        self.outbound = outbound
        self.check_crc = check_crc
        self.port_config = port_config
        self.transport = None
        self._buffer = bytearray()
        self.msgs_recvd = 0
        self.in_frame = False

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
                            DataLinkFrame(self.port_config.port_id(), frame.data, frame.hdlc_port,
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
        while True:
            frame: DataLinkFrame = await self.outbound.get()
            if frame is None:
                break
            kiss_frame = KISSFrame(frame.hldc_port, KISSCommand.Data, frame.data)
            kiss_data = encode_kiss_frame(kiss_frame, False)
            self.transport.write(kiss_data)
            self.outbound.task_done()

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
