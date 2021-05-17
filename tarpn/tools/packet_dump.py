import argparse
import asyncio
from functools import partial

import serial_asyncio

from tarpn.ax25 import decode_ax25_packet
from tarpn.port.kiss import KISSProtocol
from tarpn.settings import PortConfig


async def printer(inbound):
    while True:
        frame = await inbound.get()
        if frame is not None:
            try:
                packet = decode_ax25_packet(frame.buffer)
                print(packet)
            finally:
                inbound.task_done()


def main():
    parser = argparse.ArgumentParser(description='Decode packets from a serial port')
    parser.add_argument("port", help="Serial port to open")
    parser.add_argument("baud", type=int, help="Baudrate to use")
    parser.add_argument("--check_crc", type=bool, default=False)
    args = parser.parse_args()

    port_config = PortConfig.from_dict(0, {
        "port.enabled": True,
        "port.type": "serial",
        "serial.device": args.port,
        "serial.speed": args.baud
    })

    loop = asyncio.get_event_loop()
    in_queue: asyncio.Queue = asyncio.Queue()
    out_queue: asyncio.Queue = asyncio.Queue()
    protocol_factory = partial(KISSProtocol, loop, in_queue, out_queue, port_config.port_id(), check_crc=args.check_crc)
    protocol = serial_asyncio.create_serial_connection(loop, protocol_factory, args.port, baudrate=args.baud)
    asyncio.ensure_future(protocol)
    print('Scheduled Serial connection')

    asyncio.ensure_future(printer(in_queue))
    loop.run_forever()
    print('Done')
