import asyncio
from functools import partial
import sys

import serial_asyncio

from tarpn.port.kiss import KISSProtocol, KISSFrame, KISSCommand


async def cat(path, protocol):
    with open(path, 'rb') as fp:
        data = fp.read()

        frame = KISSFrame(0, KISSCommand.Data, data)
        protocol.write(frame)


async def async_main():
    loop = asyncio.get_event_loop()
    in_queue = asyncio.Queue()
    out_queue = asyncio.Queue()
    protocol_factory = partial(KISSProtocol, loop, in_queue, out_queue, tnc_port=0, check_crc=False)
    coro = serial_asyncio.create_serial_connection(loop, protocol_factory, '/tmp/vmodem1', baudrate=115200)
    transport, protocol = await coro
    await asyncio.ensure_future(cat(sys.argv[1], protocol))


def main():
    asyncio.run(async_main())
