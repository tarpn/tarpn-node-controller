from asyncio.protocols import Protocol
from typing import Optional

from tarpn.ax25 import AX25Call, parse_ax25_call
from tarpn.events import EventBus, EventListener
from tarpn.netrom import NetRom


class AppProtocol:
    def __init__(self):
        self.transport = None
        self.circuit_id = None

    def _on_data(self, my_circuit_id: int, remote_call: AX25Call, data: bytes, *args, **kwargs):
        payload = bytearray()
        payload.append(0x00)
        payload.append(0x01)
        remote_call.write(payload)
        payload.append(len(data))
        payload.extend(data)
        self.transport.write(payload)

    def _on_connect(self, my_circuit_id: int, remote_call: AX25Call, *args, **kwargs):
        self.circuit_id = my_circuit_id
        payload = bytearray()
        payload.append(0x00)
        payload.append(0x02)
        remote_call.write(payload)
        self.transport.write(payload)

    def _on_disconnect(self, my_circuit_id: int, remote_call: AX25Call, *args, **kwargs):
        payload = bytearray()
        payload.append(0x00)
        payload.append(0x03)
        remote_call.write(payload)
        self.transport.write(payload)


class NetromAppProtocol(AppProtocol, Protocol):
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

    def data_received(self, data: bytes):
        print("data from socket")
        """
        Receive an event from the application socket.
        """
        bytes_iter = iter(data)
        proto_version = next(bytes_iter)

        if proto_version != 0x00:
            raise RuntimeError(f"Unexpected app protocol version {proto_version}")

        message_type = next(bytes_iter)
        if message_type == 0x01:  # data
            remote_call = parse_ax25_call(bytes_iter)
            data_len = next(bytes_iter)
            data = bytes(bytes_iter)
            if len(data) != data_len:
                raise BufferError(f"Underflow exception, did not expect any more bytes here. {repr(data)}")
            if self.circuit_id is not None:
                self.network.nl_data_request(self.circuit_id, remote_call, self.app_call, data)
            else:
                self.transport.write("Not connected yet!\r\n".encode("ASCII"))
        elif message_type == 0x02:  # connect
            remote_call = parse_ax25_call(bytes_iter)
            if next(bytes_iter, None):
                raise BufferError(f"Underflow exception, did not expect any more bytes here. {repr(data)}")
            self.network.nl_connect_request(remote_call, self.app_call)
        elif message_type == 0x03:  # disconnect
            remote_call = parse_ax25_call(bytes_iter)
            if next(bytes_iter, None):
                raise BufferError(f"Underflow exception, did not expect any more bytes here. {repr(data)}")
            self.network.nl_disconnect_request(self.circuit_id, remote_call, self.app_call)
        else:  # unknown
            raise RuntimeError(f"Unknown message type {message_type}")

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
