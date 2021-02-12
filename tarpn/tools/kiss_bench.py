import argparse
import asyncio
import os
import time

from tarpn.datalink import FrameData
from tarpn.port.kiss import kiss_port_factory
from tarpn.settings import PortConfig


async def produce(count, size, queue):
    out = []
    for i in range(count):
        data = os.urandom(32)
        dl_frame = FrameData(1, data, 0)
        out.append(dl_frame)
        await asyncio.create_task(queue.put(dl_frame))
    print("Done sending")
    return out


async def consume(expected, queue):
    out = []
    for i in range(expected):
        got = await queue.get()
        queue.task_done()
        out.append(got)
    return out


async def async_main():
    parser = argparse.ArgumentParser(description='Send random data to port1 and verify it on port2')
    parser.add_argument("port1", help="Serial device 1")
    parser.add_argument("port2", help="Serial device 2")
    parser.add_argument("baud", type=int, help="Baudrate to use")
    parser.add_argument("count", type=int, default=1000, help="Number of packets to send")
    parser.add_argument("size", type=int, default=32, help="Number of random bytes per packet")

    parser.add_argument("--check_crc", type=bool, default=False)
    args = parser.parse_args()

    print("Start")

    port_1 = PortConfig.from_dict(1, {
        "port.type": "serial",
        "serial.device": args.port1,
        "serial.speed": args.baud,
        "port.framing": "kiss",
        "kiss.checksum": args.check_crc
    })

    port_2 = PortConfig.from_dict(2, {
        "port.type": "serial",
        "serial.device": args.port2,
        "serial.speed": args.baud,
        "port.framing": "kiss",
        "kiss.checksum": args.check_crc
    })
    in_queue_1: asyncio.Queue = asyncio.Queue()
    out_queue_1: asyncio.Queue = asyncio.Queue()
    await kiss_port_factory(in_queue_1, out_queue_1, port_1)

    in_queue_2: asyncio.Queue = asyncio.Queue()
    out_queue_2: asyncio.Queue = asyncio.Queue()
    await kiss_port_factory(in_queue_2, out_queue_2, port_2)

    produce_coro = produce(args.count, args.size, out_queue_1)
    consume_coro = consume(args.count, in_queue_2)

    t0 = time.time()
    sent = await produce_coro
    try:
        received = await asyncio.wait_for(consume_coro, 10)
    except asyncio.TimeoutError:
        print('Timed out!')
        exit(1)

    t1 = time.time()

    print(f"Time taken: {t1-t0:0.4f}s")
    print(f"Packets per second: {(len(sent) / (t1-t0)):0.0f}")
    print(f"Packets sent: {len(sent)}")
    print(f"Packets heard: {len(received)}")

    corrupt = 0
    for a, b in zip(sent, received):
        if a.data != b.buffer:
            corrupt += 1

    print(f"Corrupt packets: {corrupt}")


def main():
    asyncio.run(async_main())
