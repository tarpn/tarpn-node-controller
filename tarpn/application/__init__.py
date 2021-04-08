import dataclasses
import queue
from typing import Optional, Dict

import msgpack

from tarpn.app import AppPayload
from tarpn.ax25 import AX25Call, parse_ax25_call
from tarpn.log import LoggingMixin
from tarpn.transport import Transport, Protocol
from tarpn.transport.netrom_l4 import NetRomTransportProtocol


class TransportMultiplexer:
    def __init__(self):
        self.socket_transport: Optional[Transport] = None
        self.network_transports: Dict[AX25Call, Transport] = dict()  # TODO should probably use circuit not call here


class MultiplexingProtocol(Protocol):
    """
    One of these exists for each bound application. It serves as the intermediary between the app's unix socket
    and the network layer.
    """
    def __init__(self, multiplexer: TransportMultiplexer):
        self.multiplexer: TransportMultiplexer = multiplexer
        self.network_transport: Optional[Transport] = None
        self.out_queue = queue.Queue()

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

    def connection_made(self, transport: Transport) -> None:
        """
        Handle a new network connection

        :param transport:
        :return:
        """
        print(f"Creating transport for {transport.remote_call}")
        self.network_transport = transport
        if transport.remote_call not in self.multiplexer.network_transports:
            self.multiplexer.network_transports[transport.remote_call] = transport
            # This is a new connection, inform the application
            payload = AppPayload(0, 2, bytearray())
            transport.remote_call.write(payload.buffer)
            msg = msgpack.packb(dataclasses.asdict(payload))
            self.multiplexer.socket_transport.write(msg)
        else:
            self.multiplexer.network_transports[transport.remote_call] = transport

    def connection_lost(self, exc: Optional[Exception]) -> None:
        payload = AppPayload(0, 3, bytearray())
        self.network_transport.remote_call.write(payload.buffer)
        msg = msgpack.packb(dataclasses.asdict(payload))
        self.multiplexer.socket_transport.write(msg)

        del self.multiplexer.network_transports[self.network_transport.remote_call]
        self.network_transport = None


class ApplicationProtocol(Protocol, LoggingMixin):
    """
    This protocol runs inside the packet engine and is responsible for managing connections and passing
    messages to the applications over the transport (unix domain socket, or possibly tcp socket)
    """

    def __init__(self, app_name: str, app_call: AX25Call, app_alias: str,
                 transport_manager: NetRomTransportProtocol, multiplexer: TransportMultiplexer):
        Protocol.__init__(self)
        LoggingMixin.__init__(self)
        self.app_name = app_name
        self.app_call = app_call
        self.app_alias = app_alias
        self.transport_manager = transport_manager
        self.multiplexer = multiplexer
        self.unpacker = msgpack.Unpacker()
        self.out_queue = queue.Queue()

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
                self.transport_manager.nl_connect_request(remote_call, self.app_call, self.app_call, self.app_call)
            elif payload.type == 0x03:  # Disconnect
                remote_call = parse_ax25_call(bytes_iter)
                if remote_call in self.multiplexer.network_transports:
                    circuit_id = self.multiplexer.network_transports.get(remote_call).circuit_id
                    self.transport_manager.nl_disconnect_request(circuit_id, remote_call, self.app_call)
                else:
                    self.warning(f"Not connected to {remote_call}\r\n".encode("ASCII"))
            else:
                raise RuntimeError(f"Unknown message type {payload.type}")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self.info(f"Lost connection to unix socket")
        self.multiplexer.socket_transport = None

    def connection_made(self, transport: Transport):
        self.info(f"Connected to unix socket {transport.get_extra_info('sockname')}")
        self.multiplexer.socket_transport = transport
        # Need to inform the application of all existing connections
        for remote_call in self.multiplexer.network_transports.keys():
            payload = AppPayload(0, 2, bytearray())
            remote_call.write(payload.buffer)
            msg = msgpack.packb(dataclasses.asdict(payload))
            transport.write(msg)
