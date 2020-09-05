import argparse
import asyncio
import sys

from tarpn.app import Application, Context
from tarpn.ax25 import AX25Call
from tarpn.ax25.datalink import DataLinkManager
from tarpn.ax25.statemachine import AX25StateEvent
from tarpn.port import port_factory
from tarpn.settings import PortConfig


class TTY(Application):
    def __init__(self):
        self.context = None

    def on_connect(self, context: Context):
        print("connect")
        self.context = context

    def on_disconnect(self, context: Context):
        print("disconnect")

    def on_error(self, context: Context, error: str):
        print(f"error: {error}")

    def read(self, context: Context, data: bytes):
        sys.stdout.write(data.decode("ASCII"))

    def handle_stdin(self, local_call, remote_call, dlm):
        ready = sys.stdin.readline()
        if self.context:
            self.context.write(ready.encode("ASCII"))
        else:
            print(f"Connecting to {remote_call}")
            dl_connect = AX25StateEvent.dl_connect(remote_call, local_call)
            dlm.state_machine.handle_internal_event(dl_connect)


async def main_async():
    parser = argparse.ArgumentParser(description='Decode packets from a serial port')
    parser.add_argument("port", help="Serial port to open")
    parser.add_argument("baud", type=int, help="Baudrate to use")
    parser.add_argument("local_call", help="Your callsign")
    parser.add_argument("remote_call", help="Remote callsign")
    parser.add_argument("--check_crc", type=bool, default=False)
    args = parser.parse_args()

    port_config = PortConfig.from_dict(0, {
        "port.enabled": True,
        "port.type": "serial",
        "serial.device": args.port,
        "serial.speed": args.baud
    })

    in_queue: asyncio.Queue = asyncio.Queue()
    out_queue: asyncio.Queue = asyncio.Queue()
    await port_factory(in_queue, out_queue, port_config)

    # Wire the port with an AX25 layer
    tty = TTY()
    dlm = DataLinkManager(AX25Call.parse(args.local_call), port_config.port_id(), in_queue, out_queue, tty)
    loop = asyncio.get_event_loop()
    loop.add_reader(sys.stdin, tty.handle_stdin, AX25Call.parse(args.local_call), AX25Call.parse(args.remote_call), dlm)

    await dlm.start()


def main():
    asyncio.run(main_async(), debug=True)
