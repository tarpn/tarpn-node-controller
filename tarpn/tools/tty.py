import argparse
import asyncio
import logging
import signal
import sys
from time import sleep

from tarpn.app import Application, Context
from tarpn.ax25 import AX25Call, L3Protocol
from tarpn.ax25.datalink import DataLinkManager
from tarpn.ax25.statemachine import AX25StateEvent
from tarpn.main import IdHandler
from tarpn.netrom.network import NetRomNetwork
from tarpn.netrom.statemachine import NetRomStateEvent
from tarpn.port import port_factory
from tarpn.settings import PortConfig, NetworkConfig


class TTY(Application):
    def __init__(self):
        self.context = None

    def on_connect(self, context: Context):
        print(f"Connected to {context.remote_call}")
        self.context = context

    def on_disconnect(self, context: Context):
        print(f"Disconnected from {context.remote_call}")
        self.context = None

    def on_error(self, context: Context, error: str):
        print(f"Had an error: {error}")

    def read(self, context: Context, data: bytes):
        sys.stdout.write(data.decode("ASCII"))

    def handle_stdin(self, local_call, remote_call, nl):
        ready = sys.stdin.readline()
        if self.context:
            self.context.write(ready.encode("ASCII"))
        else:
            print(f"Connecting to {remote_call}")
            nl_connect = NetRomStateEvent.nl_connect(99, remote_call, local_call)
            nl.sm.handle_internal_event(nl_connect)


async def shutdown(loop):
    # Give things a chance to shutdown
    await asyncio.sleep(1)
    pending = [task for task in asyncio.Task.all_tasks() if task is not
               asyncio.tasks.Task.current_task()]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    loop.stop()


def handle_signal(dlm, tty, loop):
    if tty.context:
        print(f"Disconnecting and shutting down")
        tty.context.close()
    dlm.stop()
    asyncio.create_task(shutdown(loop))


def main():
    parser = argparse.ArgumentParser(description='Decode packets from a serial port')
    parser.add_argument("port", help="Serial port to open")
    parser.add_argument("baud", type=int, help="Baudrate to use")
    parser.add_argument("local_call", help="Your callsign")
    parser.add_argument("remote_call", help="Remote callsign")
    parser.add_argument("--check_crc", type=bool, default=False)
    parser.add_argument("--debug", action="store_true")
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
    loop.run_until_complete(port_factory(in_queue, out_queue, port_config))

    # Wire the port with an AX25 layer
    tty = TTY()
    dlm = DataLinkManager(AX25Call.parse(args.local_call), port_config.port_id(), in_queue, out_queue)

    # Wire up the network
    network_config = NetworkConfig.from_dict({
        "netrom.node.call": args.local_call,
        "netrom.node.alias": "TTY",
        "netrom.ttl": 7,
        "netrom.nodes.interval": 60,
        "netrom.obs.init": 6,
        "netrom.obs.min": 4,
        "netrom.nodes.quality.min": 74
    })
    nl = NetRomNetwork(network_config)
    nl.bind_data_link(0, dlm)
    nl.bind_application(AX25Call.parse(args.local_call), "TTY", tty)
    dlm.add_l3_handler(L3Protocol.NetRom, nl)
    dlm.add_l3_handler(L3Protocol.NoLayer3, IdHandler())

    # Setup the tty stdin reader
    loop.add_reader(sys.stdin, tty.handle_stdin, AX25Call.parse(args.local_call), AX25Call.parse(args.remote_call), nl)

    # Configure logging
    packet_logger = logging.getLogger("ax25.packet")
    if args.debug:
        packet_logger.setLevel(logging.DEBUG)
        state_logger = logging.getLogger("ax25.statemachine")
        state_logger.setLevel(logging.DEBUG)
        state_logger.addHandler(logging.StreamHandler(sys.stdout))
    else:
        packet_logger.setLevel(logging.INFO)
    packet_logger.addHandler(logging.StreamHandler(sys.stdout))

    loop.add_signal_handler(signal.SIGTERM, handle_signal, dlm, tty, loop)
    loop.add_signal_handler(signal.SIGINT, handle_signal, dlm, tty, loop)
    loop.create_task(dlm.start())

    try:
        loop.run_forever()
    finally:
        loop.close()
