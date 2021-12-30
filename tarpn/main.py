import argparse
import logging
import logging.config
import os
import shutil
import sys
from functools import partial

import tarpn.netrom.router
from tarpn.application import TransportMultiplexer, MultiplexingProtocol, ApplicationProtocol
from tarpn.application.command import NodeCommandProcessor
from tarpn.application.shell import TarpnShellProtocol
from tarpn.ax25 import AX25Call
from tarpn.datalink import L2FIFOQueue
from tarpn.datalink.ax25_l2 import AX25Protocol, DefaultLinkMultiplexer, AX25Address
from tarpn.datalink.protocol import L2IOLoop
from tarpn.io.kiss import KISSProtocol
from tarpn.io.serial import SerialDevice
from tarpn.io.udp import UDPThread, UDPWriter
from tarpn.network import L3Protocols, L3PriorityQueue
from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.header import Protocol
from tarpn.network.mesh.protocol import MeshProtocol, L4Handlers
from tarpn.network.netrom_l3 import NetRomL3
from tarpn.network.nolayer3 import NoLayer3Protocol
from tarpn.scheduler import Scheduler
from tarpn.settings import Settings
from tarpn.transport.mesh.broadcast import BroadcastProtocol
from tarpn.transport.mesh.datagram import DatagramProtocol
from tarpn.transport.mesh.fragment import FragmentProtocol
from tarpn.transport.mesh.reliable import ReliableProtocol, ReliableManager
from tarpn.transport.mesh_l4 import MeshTransportManager, MeshTransportAddress
from tarpn.transport.netrom_l4 import NetRomTransportProtocol
from tarpn.transport.unix import UnixServerThread
from tarpn.util import WallTime

logger = logging.getLogger("root")


def main():
    parser = argparse.ArgumentParser(description='Decode packets from a serial port')
    parser.add_argument("config", nargs="?", default="config/node.ini", help="Config file")
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
    # Bootstrap node.ini
    if not os.path.exists(args.config) and os.path.basename(args.config) == "node.ini":
        shutil.copyfile("config/node.ini.sample", args.config)

    # Load settings from ini file
    s = Settings(".", ["config/defaults.ini", args.config])
    node_settings = s.node_config()
    node_call = AX25Call.parse(node_settings.node_call())
    if node_call.callsign == "N0CALL":
        print("Callsign is missing from config. Please see instructions here "
              "https://github.com/tarpn/tarpn-node-controller")
        sys.exit(1)
    else:
        print(f"Loaded configuration for {node_call}")

    # Setup logging
    logging_config_file = node_settings.get("log.config", "not_set")
    if logging_config_file != "not_set":
        log_dir = node_settings.get("log.dir")
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        logging.config.fileConfig(
            logging_config_file, defaults={"log.dir": log_dir}, disable_existing_loggers=False)

    if args.verbose:
        logging.getLogger("root").setLevel(logging.DEBUG)

    # Create thread pool
    scheduler = Scheduler()

    # Initialize I/O devices and L2 protocols
    l3_protocols = L3Protocols()
    l3_protocols.register(NoLayer3Protocol())
    l2_multi = DefaultLinkMultiplexer(L3PriorityQueue, scheduler)

    # Port UDP mapping
    # udp.forwarding.enabled = true
    # udp.address = 192.168.0.160:10093
    # udp.destinations = K4DBZ-2,NODES
    # udp.mapping = KN4ORB-2:1,KA2DEW-2:2
    port_queues = {}
    if node_settings.get_boolean("udp.enabled", False):
        udp_host, udp_port = node_settings.get("udp.address").split(":")
        udp_port = int(udp_port)
        udp_writer = UDPWriter(g8bpq_address=(udp_host, udp_port))
        intercept_dests = {AX25Call.parse(c) for c in node_settings.get("udp.destinations", "").split(",")}
        interceptor = udp_writer.receive_frame
        udp_mapping = {}
        for mapping in node_settings.get("udp.mapping", "").split(","):
            c, i = mapping.split(":")
            udp_mapping[AX25Call.parse(c)] = int(i)
        scheduler.submit(UDPThread("0.0.0.0", udp_port, udp_mapping, port_queues, udp_writer))

    else:
        intercept_dests = {}
        interceptor = lambda frame: None

    for port_config in s.port_configs():
        if port_config.get_boolean("port.enabled") and port_config.get("port.type") == "serial":
            l2_queueing = L2FIFOQueue(20, AX25Protocol.maximum_frame_size())
            port_queues[port_config.port_id()] = l2_queueing
            l2 = AX25Protocol(port_config, port_config.port_id(), node_call, scheduler,
                              l2_queueing, l2_multi, l3_protocols, intercept_dests, interceptor)
            kiss = KISSProtocol(port_config.port_id(), l2_queueing, port_config.get_boolean("kiss.checksum", False))
            SerialDevice(kiss,
                         port_config.get("serial.device"),
                         port_config.get_int("serial.speed"),
                         port_config.get_float("serial.timeout"),
                         scheduler)
            scheduler.submit(L2IOLoop(l2_queueing, l2))

    # Register L3 protocols
    routing_table = tarpn.netrom.router.NetRomRoutingTable.load(
        f"nodes-{node_settings.node_call()}.json", node_settings.node_alias())

    network_configs = s.network_configs()
    if network_configs.get_boolean("netrom.enabled", False):
        logger.info("Starting NET/ROM router")
        netrom_l3 = NetRomL3(node_call, node_settings.node_alias(),
                             scheduler, l2_multi, routing_table)
        l3_protocols.register(netrom_l3)
        netrom_l4 = NetRomTransportProtocol(s.network_configs(), netrom_l3, scheduler)

    l4_handlers = L4Handlers()

    if network_configs.get_boolean("mesh.enabled", False):
        mesh_l3 = MeshProtocol(WallTime(), network_configs, l2_multi, l4_handlers, scheduler)
        l3_protocols.register(mesh_l3)

        # Create the L4 protocols
        mesh_l4 = MeshTransportManager(mesh_l3)

        # Register L4 handlers
        reliable = ReliableManager(mesh_l3, scheduler)
        fragment_protocol = FragmentProtocol(mesh_l3, mesh_l4)
        reliable_protocol = ReliableProtocol(mesh_l3, reliable, l4_handlers)
        datagram_protocol = DatagramProtocol(mesh_l3, mesh_l4, fragment_protocol, reliable_protocol)
        broadcast_protocol = BroadcastProtocol(mesh_l3, mesh_l4, reliable)
        l4_handlers.register_l4(Protocol.FRAGMENT, fragment_protocol)
        l4_handlers.register_l4(Protocol.RELIABLE, reliable_protocol)
        l4_handlers.register_l4(Protocol.DATAGRAM, datagram_protocol)
        l4_handlers.register_l4(Protocol.BROADCAST, broadcast_protocol)

        # TODO fix circular dependency here
        mesh_l4.broadcast_protocol = broadcast_protocol
        mesh_l4.datagram_protocol = datagram_protocol

        # Bind the command processor
        ncp_factory = partial(NodeCommandProcessor, config=network_configs, link=l2_multi, network=mesh_l3,
                              transport_manager=mesh_l4, scheduler=scheduler)
        mesh_l4.bind(ncp_factory, mesh_l3.our_address, 11)

        # Set up applications
        for app_config in s.app_configs():
            # We have a single unix socket connection multiplexed to many network connections
            print(app_config)
            app_multiplexer = TransportMultiplexer()
            app_address = MeshTransportAddress.parse(app_config.get("app.address"))
            app_protocol = ApplicationProtocol(
                app_config.app_name(),
                app_config.app_alias(),
                str(app_address.address),
                mesh_l4,
                app_multiplexer)
            scheduler.submit(UnixServerThread(app_config.app_socket(), app_protocol))
            multiplexer_protocol = partial(MultiplexingProtocol, app_multiplexer)
            # TODO bind or connect?
            mesh_l4.connect(multiplexer_protocol, app_address.address, MeshAddress.parse("00.a2"), app_address.port)

        sock = node_settings.get("node.sock")
        print(f"Binding node terminal to {sock}")
        scheduler.submit(UnixServerThread(sock, TarpnShellProtocol(mesh_l3, mesh_l4)))

    # Start a metrics reporter
    #reporter = ConsoleReporter(reporting_interval=300)
    #scheduler.timer(10_000, reporter.start, True)
    #scheduler.add_shutdown_hook(reporter.stop)

    logger.info("Finished Startup")
    try:
        # Wait for all threads
        scheduler.join()
    except KeyboardInterrupt:
        scheduler.shutdown()


if __name__ == "__main__":
    main()
