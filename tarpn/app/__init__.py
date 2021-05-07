import dataclasses
from asyncio.protocols import Protocol
from dataclasses import dataclass
from typing import Optional, Dict

import asyncio

import msgpack

from tarpn.ax25 import AX25Call, parse_ax25_call
from tarpn.netrom import NetRom
from tarpn.netrom.network import NetworkTransport


@dataclass
class AppPayload:
    version: int
    type: int
    address: str
    buffer: bytes


class TransportMultiplexer:
    def __init__(self):
        self.socket_transport: Optional[asyncio.Transport] = None
        self.network_transports: Dict[AX25Call, NetworkTransport] = {} # TODO should probably use circuit not call here


class MultiplexingProtocol(Protocol):
    """
    One of these exists for each bound application. It serves as the intermediary between the app's unix socket
    and the network layer.
    """
    def __init__(self, multiplexer: TransportMultiplexer):
        self.multiplexer: TransportMultiplexer = multiplexer
        self.network_transport: Optional[NetworkTransport] = None
        self.out_queue = asyncio.Queue()

    def data_received(self, data: bytes) -> None:
        """
        Handle incoming network data

        :param data:
        :return:
        """
        if self.network_transport:
            payload = AppPayload(0, 1, bytearray())
            self.network_transport.remote_call.write(payload.buffer)
            payload.buffer.extend(data)
            msg = msgpack.packb(dataclasses.asdict(payload))
            self.multiplexer.socket_transport.write(msg)
        else:
            raise RuntimeError("Should not be receiving data since there is no network transport")

    def connection_made(self, transport: NetworkTransport) -> None:
        """
        Handle a new network connection

        :param transport:
        :return:
        """
        print(f"Creating transport for {transport.remote_call}")
        self.network_transport = transport
        self.multiplexer.network_transports[transport.remote_call] = transport

        payload = AppPayload(0, 2, bytearray())
        transport.remote_call.write(payload.buffer)
        msg = msgpack.packb(dataclasses.asdict(payload))
        self.multiplexer.socket_transport.write(msg)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        payload = AppPayload(0, 3, bytearray())
        self.network_transport.local_call.write(payload.buffer)
        msg = msgpack.packb(dataclasses.asdict(payload))
        self.multiplexer.socket_transport.write(msg)

        del self.multiplexer.network_transports[self.network_transport.local_call]
        self.network_transport = None


class NetromAppProtocol(Protocol):
    """
    This protocol runs inside the packet engine and is responsible for managing connections and passing
    messages to the applications over the transport (unix domain socket, or possibly tcp socket)
    """
    def __init__(self, app_name: str, app_call: AX25Call, app_alias: str,
                 network: NetRom, multiplexer: TransportMultiplexer):
        super().__init__()
        self.app_name = app_name
        self.app_call = app_call
        self.app_alias = app_alias
        self.network = network
        self.multiplexer = multiplexer
        self.unpacker = msgpack.Unpacker()
        self.out_queue = asyncio.Queue()

    def data_received(self, data: bytes):
        """
        Receive an event from the application socket.
        """

        self.unpacker.feed(data)
        for unpacked in self.unpacker:
            print(unpacked)
            payload = AppPayload(**unpacked)
            if payload.version != 0:
                raise RuntimeError(f"Unexpected app protocol version {payload.version}")

            bytes_iter = iter(payload.buffer)
            if payload.type == 0x01:  # Data
                remote_call = parse_ax25_call(bytes_iter)
                info = bytes(bytes_iter)
                if self.multiplexer.network_transports.get(remote_call):
                    self.multiplexer.network_transports.get(remote_call).write(info)
            elif payload.type == 0x02:  # Connect
                remote_call = parse_ax25_call(bytes_iter)
                # TODO add origin node and user here
                self.network.nl_connect_request(remote_call, self.app_call, self.app_call, self.app_call)
            elif payload.type == 0x03:  # Disconnect
                remote_call = parse_ax25_call(bytes_iter)
                if remote_call in self.multiplexer.network_transports:
                    circuit_id = self.multiplexer.network_transports.get(remote_call).circuit_id
                    self.network.nl_disconnect_request(circuit_id, remote_call, self.app_call)
                else:
                    print(f"Not connected to {remote_call}\r\n".encode("ASCII"))
            else:
                raise RuntimeError(f"Unknown message type {payload.type}")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        print("Connection to socket lost")
        self.multiplexer.socket_transport = None

    def connection_made(self, transport: asyncio.BaseTransport):
        print("Connected to unix socket")
        self.multiplexer.socket_transport = transport
