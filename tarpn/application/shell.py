import time
from functools import partial
from io import StringIO
from typing import Optional, Any, TextIO

import cmd2
from cmd2 import ansi, Cmd2ArgumentParser, with_argparser, Cmd2ArgparseError

from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.protocol import MeshProtocol
from tarpn.transport import Protocol, Transport, L4Address
from tarpn.transport.mesh_l4 import MeshTransportManager
from tarpn.transport import DatagramProtocol as DProtocol


class ShellDatagramProtocol(DProtocol):
    def __init__(self, stdout: TextIO, prompt: str):
        self.stdout = stdout
        self.prompt = prompt

    def datagram_received(self, data: bytes, address: L4Address):
        self.stdout.write(f"\n{address.network_address()}:{address.port_number()} said: ")
        self.stdout.write(data.decode("utf-8").strip("\r\n"))
        self.stdout.write("\n")
        self.stdout.write(self.prompt)
        self.stdout.flush()


ping_parser = Cmd2ArgumentParser(description="Send a number of ping packets to a node in the network")
ping_parser.add_argument("-c", "--count", type=int, default=10, help="Stop sending after COUNT packets")
ping_parser.add_argument("-i", "--interval", type=int, default=1, help="Send packet every INTERVAL seconds")
ping_parser.add_argument("-W", "--timeout", type=int, default=3, help="Wait up to TIMEOUT seconds for each packet response")
ping_parser.add_argument("destination", help="Destination node")

tty_parser = Cmd2ArgumentParser(description="Connect to and send data to a node in the network")
tty_parser.add_argument("destination", help="Destination node")
tty_parser.add_argument("port", type=int, help="Destination port")


class TarpnShell(cmd2.Cmd):
    intro = "Welcome to the TARPN shell. Type help or ? to list commands.\n"
    prompt = "(tarpn) "
    file = None

    def __init__(self,
                 network: MeshProtocol,
                 transport_manager: MeshTransportManager,
                 transport: Transport,
                 *args, **kwargs):
        self.network = network
        self.transport_manager = transport_manager
        self.transport = transport
        cmd2.Cmd.__init__(self, *args, **kwargs)
        # Clean up default cmd2 behavior
        if getattr(cmd2.Cmd, "do_shell", None) is not None:
            del cmd2.Cmd.do_shell
            del cmd2.Cmd.do_py
            del cmd2.Cmd.do_ipy
            del cmd2.Cmd.do_run_script
            del cmd2.Cmd.do_run_pyscript
        self.hidden_commands.extend(["alias", "edit", "shortcuts", "EOF", "eof", "history", "macro", "set"])

        ping_parser.error = partial(self._argparse_error, ping_parser)
        ping_parser.print_help = partial(self._argparse_help, ping_parser)

        tty_parser.error = partial(self._argparse_error, tty_parser)
        tty_parser.print_help = partial(self._argparse_help, tty_parser)

        self.tty = None
        self.tty_port = None

    def _argparse_help(self, parser: Cmd2ArgumentParser):
        parser._print_message(parser.format_help(), self.stdout)

    def _argparse_error(self, parser: Cmd2ArgumentParser, message: str):
        parser.print_usage(self.stdout)
        parser.exit(2, message)

    def perror(self, msg: Any = '', *, end: str = '\n', apply_style: bool = True) -> None:
        if apply_style:
            final_msg = ansi.style_error(msg)
        else:
            final_msg = str(msg)
        ansi.style_aware_write(self.stdout, final_msg + end)

    def handle_input(self, command: bytes):
        if self.tty is not None:
            self.transport_manager.connections[self.tty_port][0].write(command)
        else:
            self.onecmd(command.decode("ascii"))

    @with_argparser(ping_parser)
    def do_ping(self, args):
        node = MeshAddress.parse(args.destination)
        self.stdout.write(f"Sending ping ({len(self.network.alive_neighbors())})...\r\n")
        for _ in range(args.count):
            t0 = time.time_ns()
            seq = self.network.ping_protocol.send_ping(node)
            found = self.network.ping_protocol.wait_for_ping(node, seq, timeout_ms=args.timeout * 1000)
            t1 = time.time_ns()
            if found:
                dt = (t1-t0) / 1000000.
                self.stdout.write(f"Got response in {dt}ms\r\n")
            else:
                self.stdout.write(f"Timed out waiting for response\r\n")

    @with_argparser(tty_parser)
    def do_tty(self, args):
        node = MeshAddress.parse(args.destination)
        port = args.port
        if node == MeshAddress.parse("ff.ff"):
            self.tty = self.transport_manager.broadcast(
                partial(ShellDatagramProtocol, self.stdout, self.prompt), self.network.our_address, port)
        else:
            self.tty = self.transport_manager.connect(
                partial(ShellDatagramProtocol, self.stdout, self.prompt), self.network.our_address, node, port)
        self.tty_port = port
        self.prompt = f"(tarpn {node}) "

    def do_neighbors(self, *args):
        "Display list of neighbors and their state"
        self.stdout.write(f"Neighbors ({len(self.network.alive_neighbors())}):\r\n")
        for neighbor in self.network.neighbors.values():
            self.stdout.write(f"name={neighbor.name} address={neighbor.address} state={neighbor.state} "
                              f"neighbors={neighbor.neighbors} last_seen={neighbor.last_seen} "
                              f"last_update={neighbor.last_update}\r\n")

    def do_links(self, *args):
        """Display link states in the network"""
        self.stdout.write(f"Link states:\r\n")
        for neighbor, link_states in self.network.valid_link_states().items():
            self.stdout.write(f"address={neighbor} name={self.network.host_names.get(neighbor)} "
                              f"epoch={self.network.link_state_epochs.get(neighbor)}\r\n")
            for link_state in link_states:
                self.stdout.write(f"\t{link_state.node} q={link_state.quality}\r\n")

    def do_bye(self, *args):
        """Close this shell"""
        self.stdout.write("Bye!\r\n")
        self.close()
        return True

    def do_EOF(self, *args):
        """Close this shell"""
        self.stdout.write("Bye!\r\n")
        self.close()
        return True

    def close(self):
        if self.transport:
            self.transport.close()
        if self.tty is not None:
            print(f"Closing {self.tty_port}")
            self.transport_manager.connections[self.tty_port][0].close()


class RedirectingStdout(StringIO):
    def __init__(self, transport: Transport):
        StringIO.__init__(self)
        self.transport = transport

    def write(self, s: str) -> int:
        self.transport.write(s.encode("ascii"))
        return len(s)

    def close(self) -> None:
        self.transport.close()
        if self.tty is not None:
            self.tty.close()


class TarpnShellProtocol(Protocol):
    def __init__(self, network: MeshProtocol, transport_manager: MeshTransportManager):
        self.network = network
        self.transport_manager = transport_manager
        self.transport: Optional[Transport] = None
        self.shell: Optional[TarpnShell] = None

    def connection_made(self, transport: Transport):
        self.transport = transport
        self.shell = TarpnShell(self.network,
                                self.transport_manager,
                                transport,
                                stdout=RedirectingStdout(transport),
                                auto_load_commands=False)
        self.transport.write(self.shell.intro.encode("ascii"))
        self.transport.write(self.shell.prompt.encode("ascii"))

    def connection_lost(self, exc):
        self.transport = None
        self.shell.close()

    def data_received(self, data: bytes):
        try:
            self.shell.handle_input(data)
        except Cmd2ArgparseError as e:
            self.transport.write(str(e).encode("ascii"))

        self.transport.write(self.shell.prompt.encode("ascii"))
