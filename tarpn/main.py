import argparse
import asyncio
import logging
import sys
from functools import partial

from tarpn.app import NetromAppProtocol
from tarpn.app.sysop import SysopInternalApp
from tarpn.ax25 import AX25Call
from tarpn.ax25.datalink import DataLinkManager, IdHandler
from tarpn.netrom.network import NetRomNetwork
from tarpn.port.kiss import kiss_port_factory
from tarpn.settings import Settings


async def main_async():
    parser = argparse.ArgumentParser(description='Decode packets from a serial port')
    parser.add_argument("config", help="Config file")
    parser.add_argument("--debug", action="store_true")

    args = parser.parse_args()

    s = Settings(".", args.config)

    dlms = []
    for port_config in s.port_configs():
        in_queue: asyncio.Queue = asyncio.Queue()
        out_queue: asyncio.Queue = asyncio.Queue()
        await kiss_port_factory(in_queue, out_queue, port_config)
        # Wire the port with an AX25 layer
        dlm = DataLinkManager(AX25Call.parse(s.node_config().node_call()), port_config.port_id(),
                              in_queue, out_queue)
        dlms.append(dlm)

    # Wire up Layer 3 and default L2 app
    nl = NetRomNetwork(s.network_configs())
    for dlm in dlms:
        nl.bind_data_link(dlm)
        dlm.add_l3_handler(IdHandler())

    # Create the main event loop
    loop = asyncio.get_event_loop()

    # Bind apps to netrom and start running the app servers
    for app_config in s.app_configs():
        nl.bind_application(AX25Call.parse(app_config.app_call()), app_config.app_alias())
        factory = partial(NetromAppProtocol, app_config.app_name(), AX25Call.parse(app_config.app_call()), app_config.app_alias(), nl)
        await loop.create_unix_server(factory, app_config.app_socket(), start_serving=True)

    # TODO add this, but configure the port
    # factory = partial(SysopInternalApp, dlms, nl)
    # await loop.create_server(factory, "0.0.0.0", 8888, start_serving=True)

    # Configure logging
    packet_logger = logging.getLogger("packet")
    packet_logger.setLevel(logging.DEBUG)
    packet_logger.addHandler(logging.StreamHandler(sys.stdout))

    event_logger = logging.getLogger("events")
    event_logger.setLevel(logging.DEBUG)
    event_logger.addHandler(logging.StreamHandler(sys.stdout))

    ax25_state_logger = logging.getLogger("ax25.statemachine")
    netrom_state_logger = logging.getLogger("netrom.statemachine")

    fh = logging.FileHandler('logs/state.log')
    fh.setLevel(logging.DEBUG)
    ax25_state_logger.addHandler(fh)
    netrom_state_logger.addHandler(fh)

    if args.debug:
        sh = logging.StreamHandler(sys.stdout)
        sh.setLevel(logging.DEBUG)
        ax25_state_logger.setLevel(logging.DEBUG)
        netrom_state_logger.setLevel(logging.DEBUG)
        ax25_state_logger.addHandler(sh)
        netrom_state_logger.addHandler(sh)

    # Start processing packets
    tasks = [dlm.start() for dlm in dlms]
    tasks.append(nl.start())
    await asyncio.wait(tasks)


def main():
    asyncio.run(main_async(), debug=True)


# Just for testing
if __name__ == "__main__":
    main()
