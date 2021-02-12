import argparse
import logging
import shlex
from functools import partial
from time import sleep
from typing import Optional, cast

from tarpn.ax25 import AX25Call
from tarpn.datalink.protocol import LinkMultiplexer
from tarpn.log import LoggingMixin
from tarpn.network.netrom_l3 import NetRomL3
from tarpn.scheduler import Scheduler
from tarpn.settings import NetworkConfig
from tarpn.transport import Protocol, Transport
from tarpn.transport.netrom_l4 import NetRomTransportProtocol, NetRomTransport


class NodeCommandProcessor(Protocol, LoggingMixin):
    def __init__(self,
                 config: NetworkConfig,
                 l2s: LinkMultiplexer,
                 l3: NetRomL3,
                 l4: NetRomTransportProtocol,
                 scheduler: Scheduler):
        self.config = config
        self.l3 = l3
        self.l2s = l2s
        self.l4 = l4
        self.scheduler = scheduler
        self.transport: Optional[Transport] = None
        self.client_transport: Optional[Transport] = None
        self.pending_open: Optional[AX25Call] = None

        self.parser = argparse.ArgumentParser(prog="TARPN", add_help=False)
        sub_parsers = self.parser.add_subparsers(title="command", required=True, dest="command")

        sub_parsers.add_parser("help", description="Print this help")
        sub_parsers.add_parser("bye", description="Disconnect from this node")
        sub_parsers.add_parser("whoami", description="Print the current user")
        sub_parsers.add_parser("hostname", description="Print the current host")
        sub_parsers.add_parser("routes", description="Print the current routing table")

        port_parser = sub_parsers.add_parser("ports", description="List available ports")
        port_parser.add_argument("--verbose", "-v", action="store_true")

        link_parser = sub_parsers.add_parser("links", description="Show existing links")
        link_parser.add_argument("--verbose", "-v", action="store_true")

        connect_parser = sub_parsers.add_parser("connect", description="Connect to a remote station")
        connect_parser.add_argument("dest", type=str, help="Destination callsign to connect to")

        def extra():
            if self.transport:
                (host, port) = self.transport.get_extra_info('peername')
                return f"[Admin {host}:{port}]"
            else:
                return ""
        LoggingMixin.__init__(self, logging.getLogger("main"), extra)
        self.info("Created NodeCommandProcessor")

    def connection_made(self, transport: Transport):
        self.info("Connection made")
        self.transport = transport

        def welcome():
            sleep(0.500)
            self.println(f"Welcome to {self.config.node_alias()} node. Enter 'help' for available commands", True)
        self.scheduler.run(welcome)

    def connection_lost(self, exc):
        self.info("Connection lost")
        self.transport = None

    def client_connection_made(self, client_transport: Transport):
        self.info(f"Opened client connection to {client_transport.get_extra_info('peername')}")
        self.println(f"Opened client connection to {client_transport.get_extra_info('peername')}", True)
        self.client_transport = client_transport
        self.pending_open = None

    def client_connection_lost(self):
        if self.client_transport:
            self.info(f"Closed client connection to {self.client_transport.local_call}")
            self.println(f"Closed client connection to {self.client_transport.local_call}", True)
            self.client_transport = None
        else:
            self.warning("No client connection exists to lose")

    def data_received(self, data: bytes) -> None:
        s = data.decode("ASCII").strip().upper()
        self.info(f"Data: {s}")

        # If we're waiting for a connection, either wait or let user BYE
        if self.pending_open is not None:
            if s == "B" or s == "BYE":
                self.println(f"Cancelling connection to {self.pending_open}", True)
                self.pending_open = None
            else:
                self.println(f"Pending connection to {self.pending_open}", True)
            return

        # If connected somewhere else, forward the input
        if self.client_transport is not None:
            if s == "B" or s == "BYE":
                self.client_transport.close()
                self.println(f"Closing connection to {self.client_transport.get_extra_info('peername')}...", True)
                self.client_transport = None
            else:
                self.client_transport.write(data)
            return

        # If not forwarding, parse the command
        try:
            parsed_args = self.parser.parse_args(shlex.split(s.lower()))
        except BaseException:
            self.println(self.parser.format_help(), True)
            return

        if parsed_args.command is None:
            parsed_args.command = "help"
        if parsed_args.command == "help":
            self.println(self.parser.format_help(), True)
        elif parsed_args.command == "ports":
            resp = "Ports:\n"
            for device_id, l2 in self.l2s.l2_devices.items():
                resp += f" - Port {device_id}: {l2.get_link_address()}\n"
            self.println(resp, True)
        elif parsed_args.command == "links":
            resp = "Links:\n"
            for link_id, l2 in self.l2s.logical_links.items():
                if l2.peer_connected(link_id):
                    resp += f" - L2 Link {link_id}, Port {l2.get_device_id()}: "
                    resp += f"{l2.get_link_address()}>{l2.get_peer_address(link_id)}\n"
            for circuit, (transport, protocol) in self.l4.l3_connections.items():
                nt = cast(NetRomTransport, transport)
                if transport == self.transport:
                    resp += f" * L4 Link {circuit}: {nt.local_call}>{nt.remote_call}\n"
                else:
                    resp += f" - L4 Link {circuit}: {nt.local_call}>{nt.remote_call}\n"
            self.println(resp, True)
        elif parsed_args.command == "routes":
            self.println("Routing Table:\n")
            self.println(str(self.l3.router), True)
        elif parsed_args.command == "whoami":
            if isinstance(self.transport, NetRomTransport):
                nt = cast(NetRomTransport, self.transport)
                self.println(f"Current user is {nt.origin_user.callsign} connected from {nt.origin_node}", True)
            else:
                self.println(f"Current user is default connected from {self.transport.get_extra_info('peername')}",
                             True)
        elif parsed_args.command == "hostname":
            self.println(f"Current host is {self.config.node_call()}", True)
        elif parsed_args.command == "bye":
            self.println("Goodbye.")
            self.transport.close()
        elif parsed_args.command == "connect":
            """
            Connect to a remote station

            Create a half-opened client connection to the remote station. Once the connect ack is received, 
            the connection will be completed and we will create the protocol and transport objects.
            """
            remote_call = AX25Call.parse(parsed_args.dest)
            local_call = AX25Call.parse(self.config.node_call())
            self.println(f"Connecting to {remote_call}...", True)

            if isinstance(self.transport, NetRomTransport):
                nt = cast(NetRomTransport, self.transport)
                self.l4.open(partial(ConnectProtocol, self), local_call, remote_call, nt.origin_node, nt.origin_user)
                self.pending_open = remote_call
            else:
                logging.warning(
                    f"Connect command is only supported for NetworkTransport, not {self.transport.__class__}")
                self.println(f"Connect command is only supported for NetworkTransport", True)

        else:
            logging.warning(f"Unhandled command {parsed_args.command}")
            self.println(f"Unhandled command {parsed_args.command}", True)

    def println(self, s: str, final=False):
        s = s.strip() + "\n"
        if not final:
            self.transport.write(s.replace("\n", "\r\n").encode("utf-8"))
        else:
            self.transport.write((s + "> ").replace("\n", "\r\n").encode("utf-8"))


class ConnectProtocol(Protocol):
    def __init__(self, command_processor: NodeCommandProcessor):
        self.command_processor = command_processor

    def data_received(self, data: bytes) -> None:
        # This is data coming back from a far away node via our CONNECT'ed channel, need to forward this data
        # back to the other end of the command processor's channel
        self.command_processor.transport.write(data)

    def connection_made(self, transport: Transport) -> None:
        self.command_processor.client_connection_made(transport)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self.command_processor.client_connection_lost()
