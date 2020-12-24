import argparse
import asyncio
import logging
from logging.handlers import TimedRotatingFileHandler
import sys
from functools import partial

from tarpn.app import NetromAppProtocol
from tarpn.app.sysop import NodeApplication, DataLinkAdapter, NetworkAdapter
from tarpn.ax25 import AX25Call
from tarpn.ax25.datalink import DataLinkManager, IdHandler
from tarpn.events import EventBus, EventListener
from tarpn.netrom.network import NetworkManager
from tarpn.port.kiss import kiss_port_factory
from tarpn.settings import Settings

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(levelname)-8s %(asctime)s -- %(message)s'))

logger = logging.getLogger("main")
logger.setLevel(logging.INFO)
logger.addHandler(handler)


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
        await kiss_port_factory(in_queue, out_queue, port_config)
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
        nl.bind(AX25Call.parse(app_config.app_call()), app_config.app_alias())
        factory = partial(NetromAppProtocol, app_config.app_name(), AX25Call.parse(app_config.app_call()), app_config.app_alias(), nl)
        await loop.create_unix_server(factory, app_config.app_socket(), start_serving=True)

    node_app_factory = partial(NodeApplication, s, dlms, nl)
    for dlm in dlms:
        dlm.add_l3_handler(DataLinkAdapter(dlm, node_app_factory))

    # Make a default application for L4
    node_call = s.network_configs().node_call()
    adaptor = NetworkAdapter(node_call, nl, node_app_factory)
    EventBus.bind(EventListener(
        f"netrom.{node_call}.inbound",
        f"netrom_{node_call}_inbound",
        adaptor.on_data
    ))
    EventBus.bind(EventListener(
        f"netrom.{node_call}.connect",
        f"netrom_{node_call}_connect",
        adaptor.on_connect
    ))
    EventBus.bind(EventListener(
        f"netrom.{node_call}.disconnect",
        f"netrom_{node_call}_disconnect",
        adaptor.on_disconnect
    ))

    if node_settings.admin_enabled():
        await loop.create_server(protocol_factory=node_app_factory,
                                 host=node_settings.admin_listen(),
                                 port=node_settings.admin_port(),
                                 start_serving=True)

    # Configure logging
    packet_logger = logging.getLogger("packet")
    packet_logger.setLevel(logging.INFO)
    packet_logger.addHandler(handler)

    event_logger = logging.getLogger("events")
    event_logger.setLevel(logging.INFO)
    event_logger.addHandler(handler)

    ax25_state_logger = logging.getLogger("ax25.state")
    netrom_state_logger = logging.getLogger("netrom.state")

    fh = TimedRotatingFileHandler('logs/state.log', when='midnight')
    fh = logging.FileHandler('logs/state.log')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter('%(levelname)-8s %(asctime)s -- %(message)s'))
    ax25_state_logger.addHandler(fh)
    netrom_state_logger.addHandler(fh)

    if args.debug:
        packet_logger.setLevel(logging.DEBUG)
        event_logger.setLevel(logging.DEBUG)
        handler.setLevel(logging.DEBUG)
        ax25_state_logger.setLevel(logging.DEBUG)
        netrom_state_logger.setLevel(logging.DEBUG)
        ax25_state_logger.addHandler(handler)
        netrom_state_logger.addHandler(handler)

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
