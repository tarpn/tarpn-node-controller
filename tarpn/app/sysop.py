import asyncio
from asyncio import Protocol, transports
from time import sleep
from typing import Optional, List

from tarpn.ax25 import AX25Call, AX25StateType, L3Protocol
from tarpn.ax25.datalink import DataLinkManager, L3Handler
from tarpn.events import EventListener, EventBus
from tarpn.netrom import NetRom
from tarpn.netrom.statemachine import NetRomStateType




class SysopL3Handler(L3Handler):
    def __init__(self, transport):
        self.transport = transport

    def can_handle(self, protocol: L3Protocol) -> bool:
        return protocol == L3Protocol.NoLayer3

    def handle(self, port: int, remote_call: AX25Call, data: bytes) -> bool:
        print(data)
        return True


class SysopInternalApp(Protocol):
    """
    This is the default application for the packet engine. If configured, it runs as a NoLayer3 L2 application as
    well as a NetRom application for the engine's node.call. Unlike other applications, this one runs within
    the packet engine since it needs access to internal things like the active links, routing table, etc.
    """
    def __init__(self, links: List[DataLinkManager], network: NetRom):
        self.transport = None
        self.links: List[DataLinkManager] = links
        self.network: NetRom = network
        self.attached_link = None
        self.attached_call = None

    def _link_data_callback(self, remote_call, protocol):
        def inner(inner_remote_call, inner_protocol, data):
            if inner_remote_call == remote_call and inner_protocol == protocol:
                self.transport.write(f"{remote_call}: ".encode("ASCII"))
                self.transport.write(data)
        return inner

    def _link_connect_callback(self, remote_call, link):
        def inner(inner_remote_call):
            if inner_remote_call == remote_call:
                self.attached_link = link
                self.attached_call = remote_call
                self.transport.write(f"Connected to {remote_call}\r\n".encode("ASCII"))
        return inner

    def _link_disconnect_callback(self, remote_call, link):
        def inner(inner_remote_call):
            if inner_remote_call == remote_call:
                self.attached_link = None
                self.attached_call = None
                self.transport.write(f"Disconnected from {remote_call}\r\n".encode("ASCII"))
                EventBus.remove(f"sysop-{link.link_call}-{remote_call}-connect")
                EventBus.remove(f"sysop-{link.link_call}-{remote_call}-disconnect")
                EventBus.remove(f"sysop-{link.link_call}-{remote_call}-inbound")
        return inner

    def data_received(self, data: bytes) -> None:
        s = data.decode("ASCII").strip().upper()

        if self.attached_link is not None:
            if s == "B" or s == "BYE":
                self.attached_link.dl_disconnect_request(self.attached_call)
                self.attached_link = None
                self.attached_call = None
            else:
                fut = self.attached_link.dl_data_request(self.attached_call, L3Protocol.NoLayer3, data)
                self.transport.write(f"You sent: {data}\r\n".encode("ASCII"))
            return

        tokens = s.split(" ")
        cmd = tokens[0]
        if cmd == "P" or cmd == "PORTS":
            resp = "Ports:\r\n"
            for dlm in self.links:
                resp += f"{dlm.link_port}: {dlm.link_call}\r\n"
            self.transport.write(resp.encode("ASCII"))
        elif cmd == "L" or cmd == "LINKS":
            resp = "Links:\r\n"
            for dlm in self.links:
                for remote_call in dlm.state_machine.get_sessions().keys():
                    if dlm.state_machine.get_state(str(remote_call)) == AX25StateType.Connected:
                        resp += f"L2 {dlm.link_call} > {str(remote_call)} on port {dlm.link_port}\r\n"
            for circuit_id in self.network.get_circuit_ids():
                circuit = self.network.get_circuit(circuit_id)
                if circuit.state == NetRomStateType.Connected:
                    resp += f"L3 {circuit.local_call} > {circuit.remote_call} on circuit {circuit_id}\r\n"
            self.transport.write(resp.encode("ASCII"))
        elif cmd == "C" or cmd == "CONNECT":
            port = int(tokens[1])
            remote_call = AX25Call.parse(tokens[2])
            found = None
            for link in self.links:
                if link.link_port == port:
                    found = link
                    break
            if found is not None:
                state = found.link_state(remote_call)
                if state == AX25StateType.Disconnected:
                    found.add_l3_handler(SysopL3Handler(self.transport))
                    self.attached_link = found
                    self.attached_call = remote_call
                    self.transport.write(f"Connecting to {remote_call}...\r\n".encode("ASCII"))
                    EventBus.bind(EventListener(
                        f"link.{found.link_call}.connect",
                        f"sysop-{found.link_call}-{remote_call}-connect",
                        self._link_connect_callback(remote_call, found)
                    ))
                    EventBus.bind(EventListener(
                        f"link.{found.link_call}.disconnect",
                        f"sysop-{found.link_call}-{remote_call}-disconnect",
                        self._link_disconnect_callback(remote_call, found)
                    ))
                    found.dl_connect_request(remote_call)
                elif state == AX25StateType.Connected:
                    found.add_l3_handler(SysopL3Handler(self.transport))
                    self.attached_link = found
                    self.attached_call = remote_call
                    self.transport.write(f"Attaching to open link to {remote_call}\r\n".encode("ASCII"))
                    EventBus.bind(EventListener(
                        f"link.{found.link_call}.inbound",
                        f"sysop-{found.link_call}-{remote_call}-inbound",
                        self._link_data_callback(remote_call, L3Protocol.NoLayer3)
                    ))
                    EventBus.bind(EventListener(
                        f"link.{found.link_call}.disconnect",
                        f"sysop-{found.link_call}-{remote_call}-disconnect",
                        self._link_disconnect_callback(remote_call, found)
                    ))
                else:
                    self.transport.write(f"Link to {remote_call} is busy\r\n".encode("ASCII"))

        else:
            self.transport.write(f"Unknown command {cmd}\r\n".encode("ASCII"))

    def connection_made(self, transport: transports.BaseTransport) -> None:
        self.transport = transport

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self.transport = None
