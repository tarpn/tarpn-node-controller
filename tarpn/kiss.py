from collections import namedtuple
from enum import IntEnum, unique

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
    def __init__(self, check_crc=False):
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
                    frame = parse_kiss_frame(self.buf, self.check_crc)
                    print(f"Got frame: {frame}")
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


def parse_kiss_frame(data, check_crc=False):
    """
    Given a full KISS frame, decode the port and command. Also un-escape the data
    :param data:
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


KISSFrame = namedtuple('KISSFrame', ['port', 'command', 'data'])


if __name__ == "__main__":
    reader = KISSReader()
    reader.data_received(bytes([KISSProtocol.FEND, KISSProtocol.FEND, KISSProtocol.FEND, KISSCommand.Data]))
    reader.data_received("TEST".encode())
    reader.data_received(bytes([KISSProtocol.FEND]))

