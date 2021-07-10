import argparse
import asyncio
import logging
import signal
import sys
from typing import Optional

from tarpn.ax25 import AX25Call
from tarpn.datalink import L2FIFOQueue
from tarpn.datalink.ax25_l2 import AX25Protocol
from tarpn.datalink.protocol import DefaultLinkMultiplexer, L2IOLoop
from tarpn.io.kiss import KISSProtocol
from tarpn.io.serial import SerialDevice
from tarpn.network import L3Protocols, L3PriorityQueue
from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.protocol import MeshProtocol
from tarpn.scheduler import Scheduler
from tarpn.settings import PortConfig
from tarpn.transport import Transport, L4Address
from tarpn.transport.mesh_l4 import MeshTransportManager
from tarpn.transport import DatagramProtocol as DProtocol
from tarpn.util import shutdown


class TTY(DProtocol):
    def __init__(self):
        self.transport: Optional[Transport] = None

    def connection_made(self, transport: Transport):
        self.transport = transport
        sys.stdout.write(f"Connected to {transport.get_extra_info('peername')}\r\n")
        sys.stdout.flush()

    def connection_lost(self, exc):
        sys.stdout.write(f"Lost connection to {self.transport.get_extra_info('peername')}\r\n")
        sys.stdout.flush()
        self.transport = None
        signal.raise_signal(signal.SIGTERM)

    def datagram_received(self, data: bytes, address: L4Address):
        msg = data.decode("utf-8")
        sys.stdout.write(f"{address}: {msg}")
        sys.stdout.flush()

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
                self.transport.write(line + "\r\n")
        else:
            sys.stdout.write("Not connected\r\n")
            sys.stdout.flush()

    def handle_signal(self, loop, scheduler):
        if self.transport is not None:
            self.transport.close()
        asyncio.create_task(shutdown(loop))
        scheduler.shutdown()


def main():
    parser = argparse.ArgumentParser(description='Broadcast to mesh network over serial device')
    parser.add_argument("device", help="Serial port to open")
    parser.add_argument("baud", type=int, help="Baudrate to use")
    parser.add_argument("callsign", help="Your callsign (e.g., K4DBZ-10)")
    parser.add_argument("address", help="Local address, e.g., 00.1a")
    parser.add_argument("port", type=int, help="Port", default=10)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    # Configure logging
    main_logger = logging.getLogger("root")
    main_logger.setLevel(logging.ERROR)
    main_logger.addHandler(logging.StreamHandler(sys.stdout))

    if args.debug:
        main_logger.setLevel(logging.DEBUG)
        state_logger = logging.getLogger("ax25.state")
        state_logger.setLevel(logging.DEBUG)
        state_logger.addHandler(logging.StreamHandler(sys.stdout))

    scheduler = Scheduler()

    # Configure and initialize I/O device and L2
    port_config = PortConfig.from_dict(0, {
        "port.enabled": True,
        "port.type": "serial",
        "serial.device": args.device,
        "serial.speed": args.baud
    })

    # Initialize I/O device and L2
    l3_protocols = L3Protocols()
    l2_multi = DefaultLinkMultiplexer(L3PriorityQueue, scheduler)
    l2_queueing = L2FIFOQueue(20, AX25Protocol.maximum_frame_size())
    l2 = AX25Protocol(port_config.port_id(), AX25Call.parse(args.callsign), scheduler,
                      l2_queueing, l2_multi, l3_protocols)
    kiss = KISSProtocol(port_config.port_id(), l2_queueing, port_config.get_boolean("kiss.checksum", False))
    SerialDevice(kiss, port_config.get("serial.device"), port_config.get_int("serial.speed"), scheduler)
    scheduler.submit(L2IOLoop(l2_queueing, l2))

    addr = MeshAddress.parse(args.address)
    mesh_l3 = MeshProtocol(
        our_address=addr,
        link_multiplexer=l2_multi,
        scheduler=scheduler)
    l3_protocols.register(mesh_l3)

    mesh_l4 = MeshTransportManager(mesh_l3)

    tty = TTY()
    loop = asyncio.get_event_loop()
    loop.add_reader(sys.stdin, tty.handle_stdin)
    loop.add_signal_handler(signal.SIGTERM, tty.handle_signal, loop, scheduler)
    loop.add_signal_handler(signal.SIGINT, tty.handle_signal, loop, scheduler)

    mesh_l4.connect(
        protocol_factory=lambda: tty,
        port=args.port,
        local_address=addr,
        remote_address=MeshProtocol.BroadcastAddress)

    try:
        loop.run_forever()
    finally:
        loop.close()


if __name__ == "__main__":
    main()
