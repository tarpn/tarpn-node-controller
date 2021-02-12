import argparse

import asyncio
import logging
import signal
import sys
from functools import partial
from typing import Optional

from tarpn.ax25 import AX25Call
from tarpn.datalink import L2FIFOQueue
from tarpn.datalink.ax25_l2 import AX25Protocol
from tarpn.datalink.protocol import LinkMultiplexer, L2IOLoop
from tarpn.io.kiss import KISSProtocol
from tarpn.io.serial import SerialDevice
from tarpn.netrom.router import NetRomRoutingTable
from tarpn.network import L3Protocols, L3PriorityQueue
from tarpn.network.netrom_l3 import NetRomL3, NetRomAddress
from tarpn.scheduler import Scheduler
from tarpn.settings import PortConfig, NetworkConfig
from tarpn.transport import Protocol, Transport
from tarpn.transport.netrom_l4 import NetRomTransportProtocol
from tarpn.util import shutdown


class TTY(Protocol):
    def __init__(self):
        self.transport: Optional[Transport] = None

    def connection_made(self, transport: Transport):
        self.transport = transport
        sys.stdout.write(f"Connected to {transport.get_extra_info('peername')}\n")
        sys.stdout.flush()

    def connection_lost(self, exc):
        sys.stdout.write(f"Lost connection to {self.transport.get_extra_info('peername')}\n")
        sys.stdout.flush()
        self.transport = None
        signal.raise_signal(signal.SIGTERM)

    def data_received(self, data: bytes):
        sys.stdout.write(data.decode("utf-8"))
        sys.stdout.flush()

    def handle_stdin(self):
        line = sys.stdin.readline()
        if self.transport is not None:
            if line == "":  # Got a ^D
                self.transport.close()
                signal.raise_signal(signal.SIGTERM)
            else:
                line = line.strip()
                self.transport.write(line)
        else:
            sys.stdout.write("Not connected\n")
            sys.stdout.flush()

    def handle_signal(self, loop, scheduler):
        if self.transport is not None:
            self.transport.close()
        asyncio.create_task(shutdown(loop))
        scheduler.shutdown()


def main():
    parser = argparse.ArgumentParser(description='Decode packets from a serial port')
    parser.add_argument("port", help="Serial port to open")
    parser.add_argument("baud", type=int, help="Baudrate to use")
    parser.add_argument("local_call", help="Your callsign (e.g., K4DBZ-10)")
    parser.add_argument("local_alias", help="Your alias (e.g., ZDBZ10)")
    parser.add_argument("remote_call", help="Remote callsign")
    parser.add_argument("-datalink", help="Force L2 mode", action="store_true")
    parser.add_argument("--check-crc", type=bool, default=False)
    parser.add_argument("--monitor-port", type=int)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    # Configure logging
    main_logger = logging.getLogger("main")
    main_logger.setLevel(logging.ERROR)
    if args.debug:
        main_logger.setLevel(logging.DEBUG)

        state_logger = logging.getLogger("ax25.state")
        state_logger.setLevel(logging.DEBUG)
        state_logger.addHandler(logging.StreamHandler(sys.stdout))

        state_logger = logging.getLogger("netrom.state")
        state_logger.setLevel(logging.DEBUG)
        state_logger.addHandler(logging.StreamHandler(sys.stdout))

    scheduler = Scheduler()

    # Configure and initialize I/O device and L2
    port_config = PortConfig.from_dict(0, {
        "port.enabled": True,
        "port.type": "serial",
        "serial.device": args.port,
        "serial.speed": args.baud
    })

    # Initialize I/O device and L2
    l3_protocols = L3Protocols()
    l2_multi = LinkMultiplexer(L3PriorityQueue, scheduler)
    l2_queueing = L2FIFOQueue(20, AX25Protocol.maximum_frame_size())
    l2 = AX25Protocol(port_config.port_id(), AX25Call.parse(args.local_call), scheduler,
                      l2_queueing, l2_multi, l3_protocols)
    kiss = KISSProtocol(port_config.port_id(), l2_queueing, port_config.get_boolean("kiss.checksum", False))
    SerialDevice(kiss, port_config.get("serial.device"), port_config.get_int("serial.speed"), scheduler)
    scheduler.submit(L2IOLoop(l2_queueing, l2))

    # Initialize L3 and L4
    network_config = NetworkConfig.from_dict({
        "netrom.node.call": args.local_call,
        "netrom.node.alias": args.local_alias,
        "netrom.ttl": 7,
        "netrom.nodes.interval": 60,
        "netrom.obs.init": 6,
        "netrom.obs.min": 4,
        "netrom.nodes.quality.min": 74
    })

    # Register L3 protocols
    netrom_l3 = NetRomL3(AX25Call.parse(network_config.node_call()), network_config.node_alias(),
                         scheduler, l2_multi, NetRomRoutingTable(network_config.node_alias()))
    l3_protocols.register(netrom_l3)

    # Create the L4 protocol
    netrom_l4 = NetRomTransportProtocol(network_config, netrom_l3, scheduler)

    async def wait_for_network():
        tty = TTY()
        loop.add_reader(sys.stdin, tty.handle_stdin)
        loop.add_signal_handler(signal.SIGTERM, tty.handle_signal, loop, scheduler)
        loop.add_signal_handler(signal.SIGINT, tty.handle_signal, loop, scheduler)

        remote_call = AX25Call.parse(args.remote_call)
        while True:
            (found, mtu) = netrom_l3.route_packet(NetRomAddress.from_call(remote_call))
            if found:
                break
            await asyncio.sleep(0.200)
        main_logger.info(f"Learned route to {remote_call}")
        await asyncio.sleep(1.200)
        netrom_l4.open(protocol_factory=lambda: tty,
                       local_call=AX25Call.parse(args.local_call),
                       remote_call=AX25Call.parse(args.remote_call),
                       origin_user=AX25Call(args.local_alias),
                       origin_node=AX25Call(args.local_call))

    loop = asyncio.get_event_loop()
    loop.create_task(wait_for_network())

    try:
        loop.run_forever()
    finally:
        loop.close()


if __name__ == "__main__":
    main()