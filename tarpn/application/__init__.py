import dataclasses
import logging
import queue
from typing import Optional, Dict

import msgpack

from tarpn.app import AppPayload
from tarpn.log import LoggingMixin
from tarpn.transport import Transport, Protocol, L4Protocol, L4Address
from tarpn.transport import DatagramProtocol as DProtocol
from tarpn.transport import DatagramTransport as DTransport


class TransportMultiplexer(LoggingMixin):
    def __init__(self):
        LoggingMixin.__init__(self, logger=logging.getLogger("app"))
        self.socket_transport: Optional[Transport] = None
        self.network_transports: Dict[str, DTransport] = dict()

    def write_to_socket(self, data: bytes) -> None:
        if self.socket_transport is not None:
            self.socket_transport.write(data)
        else:
            self.debug("Socket is not attached, not writing")


class MultiplexingProtocol(DProtocol):
    """
    One of these exists for each bound application. It serves as the intermediary between the app's unix socket
    and the network layer.
    """
    def __init__(self, multiplexer: TransportMultiplexer):
        self.multiplexer: TransportMultiplexer = multiplexer
        self.network_transport: Optional[DTransport] = None

    def datagram_received(self, data: bytes, address: L4Address) -> None:
        """
        Handle incoming network data
        """
        if self.network_transport:
            payload = AppPayload(0, 1, str(address), data)
            msg = msgpack.packb(dataclasses.asdict(payload))
            self.multiplexer.write_to_socket(msg)
        else:
            raise RuntimeError("Should not be receiving data since there is no network transport")

    def connection_made(self, transport: DTransport) -> None:
        """
        Handle a new network connection

        :param transport:
        :return:
        """
        addr = str(transport.local_address())
        print(f"Creating transport for {addr}")
        self.network_transport = transport
        if addr not in self.multiplexer.network_transports:
            self.multiplexer.network_transports[addr] = transport
            # This is a new connection, inform the application
            payload = AppPayload(0, 2, str(self.network_transport.remote_address()), bytes())
            msg = msgpack.packb(dataclasses.asdict(payload))
            self.multiplexer.write_to_socket(msg)
        else:
            self.multiplexer.network_transports[addr] = transport

    def connection_lost(self, exc: Optional[Exception]) -> None:
        payload = AppPayload(0, 3, str(self.network_transport.local_address()), bytes())
        msg = msgpack.packb(dataclasses.asdict(payload))
        self.multiplexer.write_to_socket(msg)

        del self.multiplexer.network_transports[str(self.network_transport.local_address())]
        self.network_transport = None


class ApplicationProtocol(Protocol, LoggingMixin):
    """
    This protocol runs inside the packet engine and is responsible for managing connections and passing
    messages to the applications over the transport (unix domain socket, or possibly tcp socket)
    """

    def __init__(self, app_name: str, app_alias: str,
                 transport_manager: L4Protocol, multiplexer: TransportMultiplexer):
        Protocol.__init__(self)
        LoggingMixin.__init__(self)
        self.app_name = app_name
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
            self.debug(unpacked)
            payload = AppPayload(**unpacked)
            if payload.version != 0:
                raise RuntimeError(f"Unexpected app protocol version {payload.version}")

            if payload.type == 0x01:  # Data
                info = bytes(payload.buffer)
                if self.multiplexer.network_transports.get(payload.address):
                    self.multiplexer.network_transports.get(payload.address).write_to(payload.address, info)
                else:
                    self.warning(f"No transport open for {payload.address}")
            elif payload.type == 0x02:  # Connect
                pass
                # TODO how to initiate connections here?
                # self.transport_manager.nl_connect_request(remote_call, self.app_call, self.app_call, self.app_call)
            elif payload.type == 0x03:  # Disconnect
                if payload.address in self.multiplexer.network_transports:
                    # TODO how to initiate disconnects here?
                    # circuit_id = self.multiplexer.network_transports.get(remote_call).circuit_id
                    # self.transport_manager.nl_disconnect_request(circuit_id, remote_call, self.app_call)
                    pass
                else:
                    self.warning(f"Not connected to {payload.address}\r\n".encode("ASCII"))
            else:
                raise RuntimeError(f"Unknown message type {payload.type}")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self.info(f"Lost connection to unix socket")
        self.multiplexer.socket_transport = None

    def connection_made(self, transport: Transport):
        self.info(f"Connected to unix socket {transport.local_address()}")
        self.multiplexer.socket_transport = transport
        # Need to inform the application of all existing connections
        for remote_address in self.multiplexer.network_transports.keys():
            payload = AppPayload(0, 2, remote_address, bytes())
            msg = msgpack.packb(dataclasses.asdict(payload))
            transport.write(msg)
