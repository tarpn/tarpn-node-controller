import argparse
import asyncio
from time import sleep
from typing import cast, List

from tarpn.app import Application, Context
from tarpn.app.chat import ChatApplication
from tarpn.ax25 import AX25Call, L3Protocol, AX25Packet, UIFrame
from tarpn.ax25.datalink import DataLinkManager
from tarpn.frame import L3Handler
from tarpn.netrom.network import NetRomNetwork
from tarpn.port import port_factory
from tarpn.settings import Settings


class IdHandler(L3Handler):
    def maybe_handle_special(self, port: int, packet: AX25Packet) -> bool:
        if packet.dest == AX25Call("ID") and isinstance(packet, UIFrame):
            ui = cast(UIFrame, packet)
            print(f"Got ID from {packet.source}: {ui.info}")
            return False
        elif packet.dest == AX25Call("CQ") and isinstance(packet, UIFrame):
            ui = cast(UIFrame, packet)
            print(f"Got CQ from {packet.source}: {ui.info}")
            return False
        return True


class SysopApplication(Application):
    def __init__(self):
        self.data_link_managers: List[DataLinkManager] = []

    def on_connect(self, context: Context):
        context.write("Welcome\r".encode("ASCII"))

    def on_disconnect(self, context: Context):
        super().on_disconnect(context)

    def on_error(self, context: Context, error: str):
        super().on_error(context, error)

    def read(self, context: Context, data: bytes):
        cmd = data.decode("ASCII").strip()
        if cmd == "PORTS":
            resp = "Ports:\r"
            for dlm in self.data_link_managers:
                resp += f"{dlm.link_port}: {dlm.link_call}\r"
            context.write(resp.encode("ASCII"))
        elif cmd == "BYE":
            context.write("Closing link, goodbye!\r".encode("ASCII"))
            sleep(0.1)
            context.close()
        elif cmd == "":
            context.write(b"\b")
        else:
            context.write(f"Unknown command: {cmd}\r".encode("ASCII"))


async def main_async():
    parser = argparse.ArgumentParser(description='Decode packets from a serial port')
    parser.add_argument("config", help="Config file")
    args = parser.parse_args()

    s = Settings(".", args.config)

    app = SysopApplication()
    dlms = []
    for port_config in s.port_configs():
        in_queue: asyncio.Queue = asyncio.Queue()
        out_queue: asyncio.Queue = asyncio.Queue()
        await port_factory(in_queue, out_queue, port_config)
        # Wire the port with an AX25 layer
        dlm = DataLinkManager(AX25Call.parse(s.node_config().node_call()), port_config.port_id(),
                              in_queue, out_queue, app)
        dlms.append(dlm)

    # Wire up Layer 3 and default L2 app
    nl = NetRomNetwork(s.network_configs())
    for dlm in dlms:
        nl.bind_data_link(dlm.link_port, dlm)
        dlm.add_l3_handler(L3Protocol.NetRom, nl)
        dlm.add_l3_handler(L3Protocol.NoLayer3, IdHandler())
        app.data_link_managers.append(dlm)

    nl.bind_application(AX25Call("K4DBZ", 10), "ZDBZ10", ChatApplication())

    # Start processing packets
    await asyncio.wait([dlm.start() for dlm in dlms])


def main():
    asyncio.run(main_async(), debug=True)


# Just for testing
if __name__ == "__main__":
    main()
