import asyncio
from functools import partial

import serial_asyncio

from tarpn.port.kiss import KISSProtocol
from tarpn.settings import PortConfig


async def port_factory(in_queue: asyncio.Queue, out_queue: asyncio.Queue, port_config: PortConfig):
    loop = asyncio.get_event_loop()

    if port_config.port_type() == "serial":
        protocol_factory = partial(KISSProtocol, loop, in_queue, out_queue, port_config=port_config,
                                   check_crc=port_config.get_boolean("kiss.checksum", False))
        await serial_asyncio.create_serial_connection(
            loop, protocol_factory, port_config.get("serial.device"), baudrate=port_config.get("serial.speed"))
        print(f"Created Serial Port {port_config.port_id()}")
    elif port_config.port_type() == "tcp":

        #  TODO TCP doesn't really work yet
        protocol_factory = partial(KISSProtocol, loop, in_queue, out_queue, port_config=port_config,
                                   check_crc=port_config.get_boolean("kiss.checksum", False))
        tcp_server = await loop.create_server(protocol_factory, "127.0.0.1", 8000)
        await tcp_server.start_serving()
        print(f"Created TCP Port {port_config.port_id()}")
    else:
        print(f"Ignoring unknown port type {port_config.port_type()} for port {port_config.port_id()}")
