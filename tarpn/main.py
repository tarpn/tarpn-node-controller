import argparse
import asyncio
import logging
import logging.config
from asyncio import Protocol, transports, BaseTransport, Transport
from logging.handlers import TimedRotatingFileHandler
import sys
from functools import partial
from typing import Optional

from tarpn.app import NetromAppProtocol, TransportMultiplexer, MultiplexingProtocol
from tarpn.app.sysop import CommandProcessorProtocol, DataLinkAdapter
from tarpn.ax25 import AX25Call
from tarpn.ax25.datalink import DataLinkManager, IdHandler
from tarpn.events import EventBus, EventListener
from tarpn.netrom.network import NetworkManager
from tarpn.port.kiss import kiss_port_factory
from tarpn.settings import Settings

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(levelname)-8s %(asctime)s -- %(message)s'))

logger = logging.getLogger("root")
logger.setLevel(logging.INFO)
logger.addHandler(handler)


class EchoProtocol(Protocol):
    def __init__(self):
        self.transport: Optional[Transport] = None

    def data_received(self, data: bytes) -> None:
        logger.info(f"Data received: {data}")
        if self.transport:
            self.transport.write(data)

    def connection_made(self, transport: Transport) -> None:
        peer = transport.get_extra_info("peername")
        logger.info(f"Connection made to {peer}")
        self.transport = transport

    def connection_lost(self, exc: Optional[Exception]) -> None:
        peer = self.transport.get_extra_info("peername")
        logger.info(f"Connection lost to {peer}")
        self.transport = None


async def main_async():
    parser = argparse.ArgumentParser(description='Decode packets from a serial port')
    parser.add_argument("config", help="Config file")
    parser.add_argument("--debug", action="store_true")

    args = parser.parse_args()

    s = Settings(".", args.config)
    node_settings = s.node_config()

    # Create the main event loop
    loop = asyncio.get_event_loop()

    dlms = []
    for port_config in s.port_configs():
        in_queue: asyncio.Queue = asyncio.Queue()
        out_queue: asyncio.Queue = asyncio.Queue()
        asyncio.create_task(kiss_port_factory(in_queue, out_queue, port_config))

        # Wire the port with an AX25 layer
        dlm = DataLinkManager(AX25Call.parse(node_settings.node_call()), port_config.port_id(),
                              in_queue, out_queue, loop.create_future)
        dlms.append(dlm)

    # Wire up Layer 3 and default L2 app
    nl = NetworkManager(s.network_configs(), loop)
    for dlm in dlms:
        nl.attach_data_link(dlm)
        dlm.add_l3_handler(IdHandler())

    # Bind apps to netrom and start running the app servers
    for app_config in s.app_configs():
        # This multiplexer bridges the unix socket server and the network connections
        multiplexer = TransportMultiplexer()

        # We have a single unix socket connection
        unix_factory = partial(NetromAppProtocol, app_config.app_name(), AX25Call.parse(app_config.app_call()),
                               app_config.app_alias(), nl, multiplexer)
        logger.info(f"Creating unix socket server for {app_config.app_call()} at {app_config.app_socket()}")
        await loop.create_unix_server(unix_factory, app_config.app_socket(), start_serving=True)

        # And many network connections
        network_factory = partial(MultiplexingProtocol, multiplexer)
        nl.bind_server(AX25Call.parse(app_config.app_call()), app_config.app_alias(), network_factory)

    node_app_factory = partial(CommandProcessorProtocol, s, dlms, nl)
    for dlm in dlms:
        # TODO add a bind_server thing here too?
        dlm.add_l3_handler(DataLinkAdapter(dlm, node_app_factory))

    node_call = s.network_configs().node_call()
    node_alias = s.network_configs().node_alias()

    # Make a default application for L4
    nl.bind_server(AX25Call.parse(node_call), node_alias, node_app_factory)

    if node_settings.admin_enabled():
        await loop.create_server(protocol_factory=node_app_factory,
                                 host=node_settings.admin_listen(),
                                 port=node_settings.admin_port(),
                                 start_serving=True)

    # Configure logging
    logging.config.fileConfig("config/logging.ini", disable_existing_loggers=False)

    event_logger = logging.getLogger("events")
    event_logger.setLevel(logging.INFO)
    event_logger.addHandler(handler)

    # Start processing packets
    tasks = [dlm.start() for dlm in dlms]
    tasks.append(nl.start())
    logger.info("Packet engine started")
    await asyncio.wait(tasks)


def main():
    asyncio.run(main_async(), debug=True)


# Just for testing
if __name__ == "__main__":
    main()
