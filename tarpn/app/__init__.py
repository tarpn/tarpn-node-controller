import dataclasses
from asyncio.protocols import Protocol
from dataclasses import dataclass
from typing import Optional

import asyncio

import msgpack

from tarpn.ax25 import AX25Call, parse_ax25_call
from tarpn.events import EventBus, EventListener
from tarpn.netrom import NetRom


@dataclass
class AppPayload:
    version: int
    type: int
    buffer: bytearray


class NetromAppProtocol(Protocol):
    """
    This protocol runs inside the packet engine and is responsible for managing connections and passing
    messages to the applications over the transport (unix domain socket, or possibly tcp socket)
    """
    def __init__(self, app_name: str, app_call: AX25Call, app_alias: str, network: NetRom):
        super().__init__()
        self.app_name = app_name
        self.app_call = app_call
        self.app_alias = app_alias
        self.network = network
        self.transport = None
        self.circuits = {}
        self.unpacker = msgpack.Unpacker()
        self.out_queue = asyncio.Queue()

    def data_received(self, data: bytes):
        """
        Receive an event from the application socket.
        """

        print("data_received from engine")
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
                circuit_id = self.circuits.get(remote_call)
                if circuit_id is not None:
                    self.network.nl_data_request(circuit_id, remote_call, self.app_call, info)
                else:
                    print("Not connected yet!\r\n".encode("ASCII"))
            elif payload.type == 0x02:  # Connect
                remote_call = parse_ax25_call(bytes_iter)
                self.network.nl_connect_request(remote_call, self.app_call)
            elif payload.type == 0x03:  # Disconnect
                remote_call = parse_ax25_call(bytes_iter)
                circuit_id = self.circuits.get(remote_call)
                if circuit_id is not None:
                    self.network.nl_disconnect_request(circuit_id, remote_call, self.app_call)
                else:
                    print("Not connected!\r\n".encode("ASCII"))
            else:
                raise RuntimeError(f"Unknown message type {payload.type}")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        print("Connection to socket lost")
        """Application disconnected from the socket... what now?"""
        EventBus.remove(f"netrom_{self.app_call}_inbound")
        EventBus.remove(f"netrom_{self.app_call}_connect")
        EventBus.remove(f"netrom_{self.app_call}_disconnect")
        self.transport = None

    def connection_made(self, transport):
        print("Connected to socket")
        self.transport = transport
        EventBus.bind(EventListener(
            f"netrom.{self.app_call}.inbound",
            f"netrom_{self.app_call}_inbound",
            self._on_data
        ))

        EventBus.bind(EventListener(
            f"netrom.{self.app_call}.connect",
            f"netrom_{self.app_call}_connect",
            self._on_connect
        ))

        EventBus.bind(EventListener(
            f"netrom.{self.app_call}.disconnect",
            f"netrom_{self.app_call}_disconnect",
            self._on_disconnect
        ))

        # Start sending outgoing data
        asyncio.create_task(self.start())

    async def start(self):
        while True:
            payload = await self.out_queue.get()
            if payload:
                self.transport.write(payload)

    def _on_data(self, my_circuit_id: int, remote_call: AX25Call, data: bytes, *args, **kwargs):
        payload = AppPayload(0, 1, bytearray())
        remote_call.write(payload.buffer)
        payload.buffer.extend(data)
        msg = msgpack.packb(dataclasses.asdict(payload))
        asyncio.create_task(self.out_queue.put(msg))

    def _on_connect(self, my_circuit_id: int, remote_call: AX25Call, *args, **kwargs):
        self.circuits[remote_call] = my_circuit_id
        payload = AppPayload(0, 2, bytearray())
        remote_call.write(payload.buffer)
        msg = msgpack.packb(dataclasses.asdict(payload))
        asyncio.create_task(self.out_queue.put(msg))

    def _on_disconnect(self, my_circuit_id: int, remote_call: AX25Call, *args, **kwargs):
        payload = AppPayload(0, 3, bytearray())
        remote_call.write(payload.buffer)
        msg = msgpack.packb(dataclasses.asdict(payload))
        asyncio.create_task(self.out_queue.put(msg))
        del self.circuits[remote_call]
