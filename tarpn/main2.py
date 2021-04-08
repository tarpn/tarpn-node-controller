import argparse
import logging
import logging.config
from functools import partial

from pyformance.reporters import ConsoleReporter

import tarpn.netrom.router
from tarpn.application import TransportMultiplexer, MultiplexingProtocol, ApplicationProtocol
from tarpn.application.command import NodeCommandProcessor
from tarpn.ax25 import AX25Call
from tarpn.datalink import L2FIFOQueue
from tarpn.datalink.ax25_l2 import AX25Protocol, LinkMultiplexer
from tarpn.datalink.protocol import L2IOLoop
from tarpn.io.kiss import KISSProtocol
from tarpn.io.serial import SerialDevice
from tarpn.network import L3Protocols, L3PriorityQueue
from tarpn.network.netrom_l3 import NetRomL3
from tarpn.network.nolayer3 import NoLayer3Protocol
from tarpn.scheduler import Scheduler
from tarpn.settings import Settings
from tarpn.transport.netrom_l4 import NetRomTransportProtocol
from tarpn.transport.unix import UnixServerThread

logger = logging.getLogger("root")


def main():
    parser = argparse.ArgumentParser(description='Decode packets from a serial port')
    parser.add_argument("config", help="Config file")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--profile", action="store_true", help="Attache a profiler to the process")
    args = parser.parse_args()
    if args.profile:
        import cProfile
        with cProfile.Profile() as pr:
            run_node(args)
        pr.print_stats(sort="tottime")
        pr.dump_stats(file="main2.prof")
    else:
        run_node(args)


def run_node(args):
    # Load settings from ini file
    s = Settings(".", args.config)
    node_settings = s.node_config()

    # Setup logging
    logging.config.fileConfig("config/logging.ini", disable_existing_loggers=False)

    # Create thread pool
    scheduler = Scheduler()

    # Initialize I/O devices and L2 protocols
    l3_protocols = L3Protocols()
    l2_multi = LinkMultiplexer(L3PriorityQueue, scheduler)

    for port_config in s.port_configs():
        l2_queueing = L2FIFOQueue(20, AX25Protocol.maximum_frame_size())
        l2 = AX25Protocol(port_config.port_id(), AX25Call.parse(node_settings.node_call()), scheduler,
                          l2_queueing, l2_multi, l3_protocols)

        kiss = KISSProtocol(port_config.port_id(), l2_queueing, port_config.get_boolean("kiss.checksum", False))
        SerialDevice(kiss, port_config.get("serial.device"), port_config.get_int("serial.speed"), scheduler)
        scheduler.submit(L2IOLoop(l2_queueing, l2))

    # Register L3 protocols
    routing_table = tarpn.netrom.router.NetRomRoutingTable.load(f"nodes-{s.network_configs().node_call()}.json",
                                                                s.network_configs().node_alias())
    netrom_l3 = NetRomL3(AX25Call.parse(s.network_configs().node_call()), s.network_configs().node_alias(),
                         scheduler, l2_multi, routing_table)
    l3_protocols.register(netrom_l3)
    l3_protocols.register(NoLayer3Protocol())

    # Create the L4 protocol
    netrom_l4 = NetRomTransportProtocol(s.network_configs(), netrom_l3, scheduler)

    # Bind the command processor
    ncp_factory = partial(NodeCommandProcessor, config=s.network_configs(), l2s=l2_multi, l3=netrom_l3,
                          l4=netrom_l4, scheduler=scheduler)
    netrom_l4.bind_server(AX25Call.parse(s.network_configs().node_call()), s.network_configs().node_alias(),
                          ncp_factory)

    # Set up applications
    for app_config in s.app_configs():
        # We have a single unix socket connection multiplexed to many network connections
        app_multiplexer = TransportMultiplexer()
        app_protocol = ApplicationProtocol(app_config.app_name(), AX25Call.parse(app_config.app_call()),
                                           app_config.app_alias(), netrom_l4, app_multiplexer)
        scheduler.submit(UnixServerThread(app_config.app_socket(), app_protocol))
        multiplexer_protocol = partial(MultiplexingProtocol, app_multiplexer)
        netrom_l4.bind_server(AX25Call.parse(app_config.app_call()), app_config.app_alias(), multiplexer_protocol)

    # Start a metrics reporter
    reporter = ConsoleReporter(reporting_interval=300)
    reporter.start()
    scheduler.add_shutdown_hook(reporter.stop)

    logger.info("Finished Startup")
    try:
        # Wait for all threads
        scheduler.join()
    except KeyboardInterrupt:
        scheduler.shutdown()


if __name__ == "__main__":
    main()
