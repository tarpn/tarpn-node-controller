import asyncio
import logging
import struct
from asyncio import transports, DatagramProtocol, AbstractEventLoop
from functools import partial
from typing import Optional, Tuple, Dict

from tarpn.ax25 import decode_ax25_packet, AX25Call
from tarpn.crc import crc16x25
from tarpn.datalink import FrameData, L2Queuing
from tarpn.log import LoggingMixin
from tarpn.scheduler import CloseableThread

packet_logger = logging.getLogger("packet")
udp_logger = logging.getLogger("udp")


class UDPWriter:
    def __init__(self, g8bpq_address):
        self.g8bpq_address = g8bpq_address
        self.transport: Optional[transports.DatagramTransport] = None

    def receive_frame(self, frame: FrameData):
        """
        Receive data that was intercepted off an AX.25 port and write it out the UDP connection
        """
        if self.transport is not None:
            payload = bytearray(frame.data)
            crc = crc16x25(payload)
            payload.extend(struct.pack(">H", crc))
            udp_logger.debug(f"Sending UDP {payload} to {self.g8bpq_address}")
            self.transport.sendto(payload, self.g8bpq_address)
        else:
            udp_logger.error("Not connected, dropping packet.")


class AsyncioUDPProtocolAdaptor(DatagramProtocol):
    def __init__(self, port_mapping: Dict[AX25Call, int], port_queues: Dict[int, L2Queuing], writer: UDPWriter):
        self.port_mapping = port_mapping
        self.port_queues = port_queues
        self.writer = writer

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        """
        Upon receiving a UDP payload from g8bpq:
        * Check CRC
        * Find port needed for forwarding
        * Send payload to port's outbound queue
        """
        udp_logger.debug(f"Received UDP payload {data} from {addr}")
        try:
            payload = data[0:-2]
            crc = struct.unpack(">H", data[-2:])[0]
            if crc == crc16x25(payload):
                packet = decode_ax25_packet(data[0:-2])
                port_id = self.port_mapping.get(packet.dest)
                if port_id is not None:
                    packet_logger.info(f"[Port={port_id} UDP] TX {len(packet.buffer)}: {packet}")
                    self.port_queues.get(port_id).offer_outbound(FrameData(port_id, packet.buffer))
                else:
                    packet_logger.warning(f"[Port=??? UDP] DROP {len(packet.buffer)}: {packet}")
            else:
                udp_logger.error(f"Bad CRC, got {crc}, calculated {crc16x25(payload)}")
        except Exception:
            udp_logger.exception("Had an error handling UDP data")

    def connection_made(self, transport: transports.DatagramTransport) -> None:
        udp_logger.info(f"UDP connection made {transport.get_extra_info('peername')}")
        self.writer.transport = transport

    def connection_lost(self, exc: Optional[Exception]) -> None:
        udp_logger.info(f"UDP connection lost {self.writer.transport.get_extra_info('peername')}")
        self.writer.transport = None


class UDPThread(CloseableThread, LoggingMixin):
    def __init__(self,
                 host: str,
                 port: int,
                 port_mapping: Dict[AX25Call, int],
                 port_queues: Dict[int, L2Queuing],
                 writer: UDPWriter):
        CloseableThread.__init__(self, f"UDP {host}:{port}")
        LoggingMixin.__init__(self, udp_logger)
        self.host = host
        self.port = port
        self.port_mapping = port_mapping
        self.port_queues = port_queues
        self.writer = writer
        self._loop: Optional[AbstractEventLoop] = None
        self._transport = None

    def run(self):
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._create_server())
        self._loop.close()

    def close(self):
        if self.transport is not None:
            self.debug(f"Closing UDP connection on {self.port}")
            self.transport.close()

    async def _create_server(self):
        loop = asyncio.get_event_loop()
        self.transport, protocol = await loop.create_datagram_endpoint(
            partial(AsyncioUDPProtocolAdaptor, self.port_mapping, self.port_queues, self.writer),
            local_addr=("0.0.0.0", self.port))
        self.debug(f"Finished starting UDP on {self.port}")
        while not self.transport.is_closing():
            await asyncio.sleep(1)
        self.debug(f"Closed UDP connection on {self.port}")
