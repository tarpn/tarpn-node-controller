import argparse
import asyncio
import logging
import signal
import sys
from asyncio import Queue

from tarpn.ax25 import AX25Call, L3Protocol
from tarpn.ax25.datalink import DataLinkManager, IdHandler
from tarpn.events import EventBus, EventListener
from tarpn.netrom import NetRom
from tarpn.netrom.network import NetworkManager
from tarpn.port.kiss import kiss_port_factory
from tarpn.settings import PortConfig, NetworkConfig
from tarpn.util import graceful_shutdown, shutdown


class L2TTY:
    def __init__(self, local_call: str, remote_call: str, datalink: DataLinkManager):
        self.local_call = AX25Call.parse(local_call)
        self.remote_call = AX25Call.parse(remote_call)
        self.dl = datalink
        self.stdin_queue = Queue()
        self.connected = False
        self.circuit_id = None
        EventBus.bind(EventListener(
            f"link.{local_call}.connect",
            f"link_{local_call}_connect",
            self.handle_connect
        ))
        EventBus.bind(EventListener(
            f"link.{local_call}.disconnect",
            f"link_{local_call}_disconnect",
            self.handle_disconnect
        ))
        EventBus.bind(EventListener(
            f"link.{local_call}.inbound",
            f"link_{local_call}_inbound",
            self.handle_data
        ))

    async def start(self):
        while True:
            next_stdin = await self.stdin_queue.get()
            if next_stdin is not None:
                if not self.connected:
                    print("connecting...")
                    self.dl.dl_connect_request(self.remote_call)
                else:
                    await self.dl.dl_data_request(self.remote_call, L3Protocol.NoLayer3, next_stdin.encode("utf-8"))
                self.stdin_queue.task_done()

    def handle_connect(self, remote_call: AX25Call):
        print(f"Connected to {remote_call} L2")
        sys.stdout.write("> ")
        self.connected = True

    def handle_disconnect(self, remote_call: AX25Call):
        print(f"Disconnected from {remote_call} L2")
        self.connected = False
        graceful_shutdown()

    def handle_stdin(self):
        line = sys.stdin.readline().strip()
        asyncio.create_task(self.stdin_queue.put(line))

    def handle_data(self, remote_call: AX25Call, protocol: L3Protocol, data: bytes):
        msg = str(data, 'utf-8')
        sys.stdout.write(msg)


class TTY:
    def __init__(self, my_call: str, my_alias: str, remote_call, nl: NetRom):
        self.local_call = AX25Call.parse(my_call)
        self.local_alias = AX25Call(callsign=my_alias)
        self.remote_call = AX25Call.parse(remote_call)
        self.nl = nl
        self.stdin_queue = Queue()
        self.connected = False
        self.circuit_id = None
        EventBus.bind(EventListener(
            f"netrom.{my_call}.connect",
            f"netrom_{my_call}_connect",
            self.handle_connect
        ))
        EventBus.bind(EventListener(
            f"netrom.{my_call}.disconnect",
            f"netrom_{my_call}_disconnect",
            self.handle_disconnect
        ))
        EventBus.bind(EventListener(
            f"netrom.{my_call}.inbound",
            f"netrom_{my_call}_inbound",
            self.handle_data
        ))


    async def start(self):
        while True:
            next_stdin = await self.stdin_queue.get()
            if next_stdin is not None:
                if not self.connected:
                    print("connecting...")
                    self.nl.nl_connect_request(self.remote_call, self.local_call, self.local_call, self.local_alias)
                else:
                    await self.nl.nl_data_request(self.circuit_id, self.remote_call, self.local_call, next_stdin.encode("utf-8"))
                self.stdin_queue.task_done()

    def handle_connect(self, circuit_id: int, remote_call: AX25Call):
        print(f"Connected to {remote_call} on circuit {circuit_id}")
        sys.stdout.write("> ")
        sys.stdout.flush()
        self.connected = True
        self.circuit_id = circuit_id

    def handle_disconnect(self, circuit_id: int, remote_call: AX25Call):
        print(f"Disconnected from {remote_call}")
        self.connected = False
        self.circuit_id = None
        graceful_shutdown()

    def handle_stdin(self):
        line = sys.stdin.readline()
        if line == "":  # Got a ^D
            if self.connected:
                self.nl.nl_disconnect_request(self.circuit_id, self.remote_call, self.local_call)
        else:
            line = line.strip()
            asyncio.create_task(self.stdin_queue.put(line))

    def handle_data(self, circuit_id: int, remote_call: AX25Call, data: bytes):
        msg = str(data, 'utf-8')
        sys.stdout.write(msg)
        sys.stdout.write("> ")
        sys.stdout.flush()


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
    parser.add_argument("-datalink", help="Force L2 mode", action="store_true")
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
    loop.create_task(kiss_port_factory(in_queue, out_queue, port_config))

    # Wire the port with an AX25 layer
    dlm = DataLinkManager(AX25Call.parse(args.local_call), port_config.port_id(), in_queue, out_queue, loop.create_future)
    dlm.add_l3_handler(IdHandler())

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
    nl = NetworkManager(network_config, loop)
    nl.attach_data_link(dlm)
    tty = TTY(args.local_call, args.local_alias, args.remote_call, nl)

    loop.create_task(tty.start())
    loop.add_reader(sys.stdin, tty.handle_stdin)

    #server = loop.run_until_complete(loop.create_server(lambda: Monitor(), '127.0.0.1', 8889))
    #loop.create_task(server.serve_forever())

    # Configure logging
    main_logger = logging.getLogger("root")
    main_logger.setLevel(logging.ERROR)
    #packet_logger.addHandler(logging.StreamHandler(sys.stdout))

    if args.debug:
        main_logger.setLevel(logging.DEBUG)

        state_logger = logging.getLogger("ax25.state")
        state_logger.setLevel(logging.DEBUG)
        state_logger.addHandler(logging.StreamHandler(sys.stdout))

        state_logger = logging.getLogger("netrom.state")
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


if __name__ == "__main__":
    main()
