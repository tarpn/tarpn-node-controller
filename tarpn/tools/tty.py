import argparse
import asyncio
import logging
import signal
import sys
from asyncio import transports
from typing import Optional

from tarpn.ax25 import AX25Call
from tarpn.ax25.datalink import DataLinkManager, IdHandler
from tarpn.events import EventBus, EventListener
from tarpn.netrom import NetRom
from tarpn.netrom.network import NetworkManager
from tarpn.port.kiss import kiss_port_factory
from tarpn.settings import PortConfig, NetworkConfig


class TTY:
    def __init__(self, app_call: str):
        self.connected = False
        EventBus.bind(EventListener(
            f"netrom.{app_call}.connect",
            f"netrom_{app_call}_connect",
            self.handle_connect
        ))
        EventBus.bind(EventListener(
            f"netrom.{app_call}.disconnect",
            f"netrom_{app_call}_disconnect",
            self.handle_disconnect
        ))

    def handle_connect(self, circuit_id: int, remote_call: AX25Call):
        print(f"Connected to {remote_call} on circuit {circuit_id}")
        self.connected = True
        self.circuit_id = circuit_id

    def handle_disconnect(self, circuit_id: int, remote_call: AX25Call):
        print(f"Disconnected from {remote_call}")
        self.connected = False
        self.circuit_id = None

    def handle_stdin(self, local_call, remote_call, nl: NetRom):
        ready = sys.stdin.readline()
        if not self.connected:
            print("connecting...")
            #  TODO maybe use an event here?
            nl.nl_connect_request(remote_call, local_call)
        else:
            nl.nl_data_request(self.circuit_id, remote_call, local_call, ready.encode("ASCII"))

    def handle_data(self, circuit_id: int, remote_call: AX25Call, data: bytes):
        print(f"{remote_call} said {data.decode('ASCII')}")


class Monitor(asyncio.Protocol):
    def __init__(self):
        self.transport = None
        self.client_host = None
        self.client_port = None

    def connection_made(self, transport: transports.BaseTransport) -> None:
        (host, port) = transport.get_extra_info('peername')
        self.transport = transport
        self.client_host = host
        self.client_port = port
        EventBus.bind(EventListener(
            "packet",
            f"tcp.monitor.{port}",
            lambda event: transport.write(repr(event).encode("ASCII"))))

    def connection_lost(self, exc: Optional[Exception]) -> None:
        EventBus.remove(f"tcp.monitor.{self.client_port}")

    def eof_received(self) -> Optional[bool]:
        EventBus.remove(f"tcp.monitor.{self.client_port}")
        return


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
    dlm.stop()
    asyncio.create_task(shutdown(loop))


def main():
    parser = argparse.ArgumentParser(description='Decode packets from a serial port')
    parser.add_argument("port", help="Serial port to open")
    parser.add_argument("baud", type=int, help="Baudrate to use")
    parser.add_argument("local_call", help="Your callsign (e.g., K4DBZ-10)")
    parser.add_argument("local_alias", help="Your alias (e.g., ZDBZ10)")
    parser.add_argument("remote_call", help="Remote callsign")
    parser.add_argument("--check-crc", type=bool, default=False)
    parser.add_argument("--monitor-port", type=int)
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
    loop.run_until_complete(kiss_port_factory(in_queue, out_queue, port_config))

    # Wire the port with an AX25 layer
    tty = TTY(args.local_call)
    dlm = DataLinkManager(AX25Call.parse(args.local_call), port_config.port_id(), in_queue, out_queue, loop.create_future)

    # Wire up the network
    network_config = NetworkConfig.from_dict({
        "netrom.node.call": args.local_call,
        "netrom.node.alias": args.local_alias,
        "netrom.ttl": 7,
        "netrom.nodes.interval": 60,
        "netrom.obs.init": 6,
        "netrom.obs.min": 4,
        "netrom.nodes.quality.min": 74
    })
    nl = NetworkManager(network_config)
    nl.bind_data_link(dlm)
    dlm.add_l3_handler(IdHandler())

    # Setup the tty stdin reader
    loop.add_reader(sys.stdin, tty.handle_stdin, AX25Call.parse(args.local_call), AX25Call.parse(args.remote_call), nl)

    # Setup stdout writer
    EventBus.bind(EventListener(
        f"netrom.{AX25Call.parse(args.local_call)}.inbound",
        f"netrom_{AX25Call.parse(args.local_call)}_inbound",
        tty.handle_data
    ))

    #server = loop.run_until_complete(loop.create_server(lambda: Monitor(), '127.0.0.1', 8889))
    #loop.create_task(server.serve_forever())

    # Configure logging
    packet_logger = logging.getLogger("packet")
    packet_logger.setLevel(logging.INFO)
    #packet_logger.addHandler(logging.StreamHandler(sys.stdout))

    if args.debug:
        packet_logger.setLevel(logging.DEBUG)
        state_logger = logging.getLogger("ax25.statemachine")
        state_logger.setLevel(logging.DEBUG)
        state_logger.addHandler(logging.StreamHandler(sys.stdout))

        state_logger = logging.getLogger("netrom.statemachine")
        state_logger.setLevel(logging.DEBUG)
        state_logger.addHandler(logging.StreamHandler(sys.stdout))

    loop.add_signal_handler(signal.SIGTERM, handle_signal, dlm, tty, loop)
    loop.add_signal_handler(signal.SIGINT, handle_signal, dlm, tty, loop)
    loop.create_task(dlm.start())
    loop.create_task(nl.start())

    try:
        loop.run_forever()
    finally:
        loop.close()
