import argparse
import logging
from asyncio import Protocol, Transport
import shlex
from functools import partial
from typing import Optional, List, Any, Dict, Callable, cast

from tarpn.ax25 import AX25Call, AX25StateType, L3Protocol
from tarpn.ax25.datalink import DataLinkManager, L3Handler
from tarpn.events import EventListener, EventBus
from tarpn.log import LoggingMixin
from tarpn.netrom import NetRom
from tarpn.netrom.network import NetworkTransport
from tarpn.netrom.statemachine import NetRomStateType
from tarpn.settings import Settings


class DataLinkTransport(Transport):
    def __init__(self, datalink: DataLinkManager, remote_call: AX25Call):
        Transport.__init__(self, extra={"peername": (str(remote_call), -1)})
        self.datalink = datalink
        self.remote_call = remote_call

    def write(self, data: Any) -> None:
        self.datalink.dl_data_request(self.remote_call, L3Protocol.NoLayer3, data)

    def close(self) -> None:
        # TODO what about connectionless?
        self.datalink.dl_disconnect_request(self.remote_call)


class DataLinkAdapter(L3Handler):
    def __init__(self, datalink: DataLinkManager, protocol_factory: Callable[[], Protocol]):
        self.datalink = datalink
        self.protocol_factory = protocol_factory
        self.app_instances: Dict[AX25Call, Protocol] = dict()

    def can_handle(self, protocol: L3Protocol) -> bool:
        return protocol == L3Protocol.NoLayer3

    def handle(self, port: int, remote_call: AX25Call, data: bytes) -> bool:
        app = self.app_instances.get(remote_call)
        if not app:
            app = self.protocol_factory()
            transport = DataLinkTransport(self.datalink, remote_call)
            app.connection_made(transport)
            self.app_instances[remote_call] = app
        app.data_received(data)
        return True


class CommandProcessorProtocol(Protocol, LoggingMixin):
    """
    This is the default application for the packet engine. If configured, it runs as a NoLayer3 L2 application as
    well as a NetRom application for the engine's node.call. Unlike other applications, this one runs within
    the packet engine since it needs access to internal things like the active links, routing table, etc.
    """
    def __init__(self, settings: Settings, links: List[DataLinkManager], network: NetRom):
        self.settings: Settings = settings
        self.transport: Optional[Transport] = None
        self.client_transport: Optional[NetworkTransport] = None
        self.datalinks: List[DataLinkManager] = links
        self.network: NetRom = network
        self.pending_open: Optional[AX25Call] = None

        parser = argparse.ArgumentParser(prog="TARPN", add_help=False)
        sub_parsers = parser.add_subparsers(title="command", required=True, dest="command")

        sub_parsers.add_parser("help", description="Print this help")
        sub_parsers.add_parser("bye", description="Disconnect from this node")
        sub_parsers.add_parser("whoami", description="Print the current user")
        sub_parsers.add_parser("hostname", description="Print the current host")

        port_parser = sub_parsers.add_parser("ports", description="List available ports")
        port_parser.add_argument("--verbose", "-v", action="store_true")

        link_parser = sub_parsers.add_parser("links", description="Show existing links")
        link_parser.add_argument("--verbose", "-v", action="store_true")

        connect_parser = sub_parsers.add_parser("connect", description="Connect to a remote station")
        connect_parser.add_argument("dest", type=str, help="Destination callsign to connect to")

        self.parser = parser

        def extra():
            if self.transport:
                (host, port) = self.transport.get_extra_info('peername')
                return f"[Admin {host}:{port}]"
            else:
                return ""

        LoggingMixin.__init__(self, logging.getLogger("main"), extra)
        self.info("Created CommandProcessorProtocol")

    def println(self, s: str, final=False):
        s = s.strip() + "\n"
        if not final:
            self.transport.write(s.replace("\n", "\r\n").encode("utf-8"))
        else:
            self.transport.write((s + "> ").replace("\n", "\r\n").encode("utf-8"))

    def data_received(self, data: bytes) -> None:
        s = data.decode("ASCII").strip().upper()
        self.info(f"Data: {s}")

        if self.pending_open is not None:
            self.println(f"Pending connection to {self.pending_open}")

        # If connected somewhere else, forward the input
        if self.client_transport:
            if s == "B" or s == "BYE":
                self.client_transport.close()
                self.println(f"Closing connection to {self.client_transport.remote_call}...", True)
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
            for dlm in self.datalinks:
                resp += f"{dlm.link_port}: {dlm.link_call}\n"
            self.println(resp, True)
        elif parsed_args.command == "links":
            resp = "Links:\n"
            for dlm in self.datalinks:
                for remote_call in dlm.state_machine.get_sessions().keys():
                    if dlm.state_machine.get_state(str(remote_call)) == AX25StateType.Connected:
                        resp += f"L2 {dlm.link_call} > {str(remote_call)} on port {dlm.link_port}\n"
            for circuit_id in self.network.get_circuit_ids():
                circuit = self.network.get_circuit(circuit_id)
                if circuit.state == NetRomStateType.Connected:
                    resp += f"L3 {circuit.local_call} > {circuit.local_call} on circuit {circuit_id}\n"
            self.println(resp, True)
        elif parsed_args.command == "whoami":
            if isinstance(self.transport, NetworkTransport):
                nt = cast(NetworkTransport, self.transport)
                self.println(f"Current user is {nt.origin_user.callsign} connected from {nt.origin_node}", True)
            else:
                self.println(f"Current user is default connected from {self.transport.get_extra_info('peername')}", True)
        elif parsed_args.command == "hostname":
            self.println(f"Current host is {self.network.local_call()}", True)
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
            local_call = AX25Call.parse(self.settings.network_configs().node_call())
            self.println(f"Connecting to {remote_call}...", True)

            if isinstance(self.transport, NetworkTransport):
                nt = cast(NetworkTransport, self.transport)
                self.network.open(partial(ConnectProtocol, self), local_call, remote_call,
                                  nt.origin_node, nt.origin_user)
                self.pending_open = remote_call
            else:
                logging.warning(f"Connect command is only supported for NetworkTransport, not {self.transport.__class__}")
                self.println(f"Connect command is only supported for NetworkTransport", True)

        else:
            logging.warning(f"Unhandled command {parsed_args.command}")
            self.println(f"Unhandled command {parsed_args.command}", True)

    def connection_made(self, transport: Transport) -> None:
        self.transport = transport
        self.info("Connection made")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self.info("Connection closed")
        self.transport = None

    def client_connection_made(self, client_transport: NetworkTransport):
        self.println(f"Opened client connection to {client_transport.remote_call}", True)
        self.client_transport = client_transport

    def client_connection_lost(self):
        if self.client_transport:
            self.println(f"Closed client connection to {self.client_transport.local_call}", True)
            self.client_transport = None
        else:
            self.warning("No client connection exists to lose")


class ConnectProtocol(Protocol):
    def __init__(self, command_processor: CommandProcessorProtocol):
        self.command_processor = command_processor

    def data_received(self, data: bytes) -> None:
        # This is data coming back from a far away node via our CONNECTed channel, need to forward this data
        # back to the other end of the command processor's channel
        self.command_processor.transport.write(data)

    def connection_made(self, transport: NetworkTransport) -> None:
        self.command_processor.client_connection_made(transport)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self.command_processor.client_connection_lost()
