import argparse
import logging
from asyncio import Protocol, Transport
import shlex
from typing import Optional, List, Any, Dict, Callable

import asyncio

from tarpn.ax25 import AX25Call, AX25StateType, L3Protocol
from tarpn.ax25.datalink import DataLinkManager, L3Handler
from tarpn.events import EventListener, EventBus
from tarpn.logging import LoggingMixin
from tarpn.netrom import NetRom
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


class NetworkTransport(Transport):
    def __init__(self, network: NetRom, local_call: AX25Call, remote_call: AX25Call, circuit_id: int):
        Transport.__init__(self, extra={"peername": (str(remote_call), circuit_id)})
        self.network = network
        self.remote_call = remote_call
        self.local_call = local_call
        self.circuit_id = circuit_id

    def write(self, data: Any) -> None:
        self.network.nl_data_request(self.circuit_id, self.remote_call, self.local_call, data)

    def close(self) -> None:
        # TODO what about connectionless?
        self.network.nl_disconnect_request(self.circuit_id, self.remote_call, self.local_call)


class NetworkAdapter:
    def __init__(self, local_call: AX25Call, network: NetRom, protocol_factory: Callable[[], Protocol]):
        self.local_call = local_call
        self.network = network
        self.protocol_factory = protocol_factory
        self.app_instances: Dict[int, Protocol] = dict()

    def on_data(self, my_circuit_id: int, remote_call: AX25Call, data: bytes, *args, **kwargs):
        protocol = self.app_instances.get(my_circuit_id)
        if protocol:
            protocol.data_received(data)
        else:
            pass  # TODO error?

    def on_connect(self, my_circuit_id: int, remote_call: AX25Call, *args, **kwargs):
        protocol = self.protocol_factory()
        transport = NetworkTransport(self.network, self.local_call, remote_call, my_circuit_id)
        protocol.connection_made(transport)
        self.app_instances[my_circuit_id] = protocol

    def on_disconnect(self, my_circuit_id: int, remote_call: AX25Call, *args, **kwargs):
        protocol = self.app_instances.get(my_circuit_id)
        if protocol:
            protocol.connection_lost(None)
            del self.app_instances[my_circuit_id]
        else:
            pass  # TODO error?

class NodeApplication(Protocol, LoggingMixin):
    """
    This is the default application for the packet engine. If configured, it runs as a NoLayer3 L2 application as
    well as a NetRom application for the engine's node.call. Unlike other applications, this one runs within
    the packet engine since it needs access to internal things like the active links, routing table, etc.
    """
    def __init__(self, settings: Settings, links: List[DataLinkManager], network: NetRom):
        self.settings: Settings = settings
        self.transport: Optional[Transport] = None
        self.datalinks: List[DataLinkManager] = links
        self.network: NetRom = network
        self.attached_circuit = None
        self.attached_remote_call = None
        self.attached_local_call = None  # This is probably always the node call.

        parser = argparse.ArgumentParser(prog="TARPN", add_help=False)
        sub_parsers = parser.add_subparsers(title="command", required=True, dest="command")

        sub_parsers.add_parser("help", description="Print this help")
        sub_parsers.add_parser("bye", description="Disconnect from this node")

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

    def _link_data_callback(self, local_call):
        def inner(circuit_id, remote_call, data):
            self.println(f"{remote_call}@{circuit_id}: ")
            self.transport.write(data)
        return inner

    def _link_connect_callback(self, local_call):
        def inner(circuit_id, remote_call):
            print(f"{local_call} connected to {remote_call} on circuit {circuit_id}")
            self.attached_circuit = circuit_id
            self.attached_remote_call = remote_call
            self.attached_local_call = local_call
            self.println(f"Connected to {remote_call}")
            EventBus.bind(EventListener(
                f"netrom.{local_call}.inbound",
                f"sysop-{local_call}-inbound",
                self._link_data_callback(local_call)
            ))
        return inner

    def _link_disconnect_callback(self, local_call):
        def inner(remote_call):
            self.attached_circuit = None
            self.attached_remote_call = None
            self.attached_local_call = None
            self.println(f"Disconnected from {remote_call}")
            EventBus.remove(f"sysop-{local_call}-connect")
            EventBus.remove(f"sysop-{local_call}-disconnect")
            EventBus.remove(f"sysop-{local_call}-inbound")
        return inner

    def println(self, s: str, final=False):
        if not final:
            self.transport.write(s.replace("\n", "\r\n").encode("utf-8"))
        else:
            self.transport.write((s + "> ").replace("\n", "\r\n").encode("utf-8"))

    def data_received(self, data: bytes) -> None:
        s = data.decode("ASCII").strip().upper()
        self.info(f"Data: {s}")

        # If connected somewhere else, forward the input
        if self.attached_circuit is not None:
            if s == "B" or s == "BYE":
                self.network.nl_disconnect_request(self.attached_circuit,
                                                   self.attached_remote_call,
                                                   self.attached_local_call)
                self.attached_circuit = None
                self.attached_remote_call = None
                EventBus.remove(f"sysop-{self.attached_local_call}-connect")
                EventBus.remove(f"sysop-{self.attached_local_call}-disconnect")
                EventBus.remove(f"sysop-{self.attached_local_call}-inbound")
            else:
                self.network.nl_data_request(self.attached_circuit,
                                             self.attached_remote_call,
                                             self.attached_local_call,
                                             data)
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
                    resp += f"L3 {circuit.local_call} > {circuit.remote_call} on circuit {circuit_id}\n"
            self.println(resp, True)
        elif parsed_args.command == "bye":
            self.println("Goodbye.")
            self.transport.close()
        elif parsed_args.command == "connect":
            """
            Connect to a remote station
            
            If L2, cannot use an existing open connection as this would break higher layers. If a port is configured
            but not connected, we can use it. Though this seems uncommon
            
            For L4 connections, just use the normal routing stuff.
            
            Maybe we can allow a way to specify a route somehow?
            """
            remote_call = AX25Call.parse(parsed_args.dest)
            local_call = AX25Call.parse(self.settings.network_configs().node_call())
            self.println(f"Connecting to {remote_call}...", True)
            self.network.nl_connect_request(remote_call, local_call)
            EventBus.bind(EventListener(
                f"netrom.{local_call}.connect",
                f"sysop-{local_call}-connect",
                self._link_connect_callback(local_call)
            ))
            EventBus.bind(EventListener(
                f"netrom.{local_call}.disconnect",
                f"sysop-{local_call}-disconnect",
                self._link_disconnect_callback(local_call)
            ))

        else:
            logging.warning(f"Unhandled command {parsed_args.command}")
            self.println(f"Unhandled command {parsed_args.command}", True)

    def connection_made(self, transport: Transport) -> None:
        self.transport = transport
        self.info("Connection made")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self.info("Connection closed")
        self.transport = None
