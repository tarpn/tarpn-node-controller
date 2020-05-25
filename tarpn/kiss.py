from typing import NamedTuple
from enum import IntEnum, unique
from functools import partial

import asyncio


@unique
class KISSProtocol(IntEnum):
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


class KISSReader(asyncio.Protocol):
    def __init__(self, frame_consumer, check_crc=False):
        self.frame_consumer = frame_consumer
        self.check_crc = check_crc
        self.transport = None
        self.buf = bytes()
        self.msgs_recvd = 0
        self.in_frame = False

    def connection_made(self, transport):
        self.transport = transport
        print('Reader connection created')

    def data_received(self, data):
        """
        Collect data until a whole frame has been read (FEND to FEND)
        """
        for b in data:
            if b == KISSProtocol.FEND:
                if self.in_frame:
                    frame = decode_kiss_frame(self.buf, self.check_crc)
                    self.frame_consumer(frame)
                    self.msgs_recvd += 1
                    self.buf = bytes()
                else:
                    # keep consuming sequential FENDs
                    continue
            else:
                if not self.in_frame:
                    self.in_frame = True
                self.buf += b.to_bytes(1, 'big')

    def connection_lost(self, exc):
        print('Reader closed')


class KISSWriter(asyncio.Protocol):

    def connection_made(self, transport):
        """Store the serial transport and schedule the task to send data.
        """
        self.transport = transport
        print('Writer connection created')
        print('Writer.send() scheduled')

    def connection_lost(self, exc):
        print('Writer closed')

    def send(self, kiss_frame):
        asyncio.ensure_future(self.send())


    async def do_send(self, data):
        self.transport.serial.write(data)


class KISSFrame(NamedTuple):
    port: int
    command: KISSCommand
    data: bytes


def encode_kiss_frame(frame: KISSFrame, include_crc=False):
    """
    Given a KISSFrame, encode into bytes for sending over an asynchronous transport
    :param frame:
    :param include_crc:
    :return:
    """
    crc = 0
    out = bytes([KISSProtocol.FEND])
    command_byte = ((frame.port << 4) & 0xF0) | (frame.command & 0x0F);
    out += int.to_bytes(command_byte, 1, 'big')
    for b in frame.data:
        crc ^= b
        if b == KISSProtocol.FEND:
            out += bytes([KISSProtocol.FESC, KISSProtocol.TFEND])
        elif b == KISSProtocol.FESC:
            out += bytes([KISSProtocol.FESC, KISSProtocol.TFESC])
        else:
            out += bytes([b])
    if include_crc:
        out += bytes([crc & 0xFF])
    out += bytes([KISSProtocol.FEND])
    return out


def decode_kiss_frame(data, check_crc=False):
    """
    Given a full KISS frame, decode the port and command. Also un-escape the data
    :param data:
    :param check_crc:
    :return:
    """
    crc = 0
    first_byte = data[0]
    crc ^= first_byte
    hdlc_port = (first_byte >> 4) & 0x0F
    kiss_command = KISSCommand.from_int(first_byte & 0x0F)
    in_escape = False
    decoded = bytes()
    for b in data[1:]:
        if b == KISSProtocol.FESC:
            in_escape = True
        else:
            if in_escape:
                if b == KISSProtocol.TFEND:
                    decoded += KISSProtocol.FEND.to_bytes(1, 'big')
                elif b == KISSProtocol.TFESC:
                    decoded += KISSProtocol.FESC.to_bytes(1, 'big')
                in_escape = False
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
            return None
    else:
        return KISSFrame(hdlc_port, kiss_command, decoded)


if __name__ == "__main__":
    def printer(frame):
        print(f"Got frame {frame}")

    reader = KISSReader(printer, check_crc=False)
    reader.data_received(bytes([KISSProtocol.FEND, KISSProtocol.FEND, KISSProtocol.FEND, KISSCommand.Data]))
    reader.data_received("TEST".encode())
    reader.data_received(bytes([KISSProtocol.FEND]))