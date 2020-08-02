import asyncio
from dataclasses import dataclass
from typing import Callable, Dict

from tarpn.ax25 import AX25Packet, AX25Call


class L3Handler:
    def maybe_handle_special(self, packet: AX25Packet) -> bool:
        """
        Handle a special packet at L2
        :param packet:
        :return: True if this packet was fully handled, False if it should continue processing
        """
        return True

    def handle(self, port: int, remote_call: AX25Call, data: bytes):
        raise NotImplemented


@dataclass
class DataLinkFrame:
    port: int
    data: bytes
    hldc_port: int = 0
    write_callback: Callable[[bytes], None] = lambda _: None


class DataLinkMultiplexer:
    def __init__(self):
        self.ports: Dict[int, asyncio.Queue] = {}

    def add_port(self, port_id: int, port_queue: asyncio.Queue):
        self.ports[port_id] = port_queue

    def write_to_port(self, port_id: int, frame: DataLinkFrame):
        queue = self.ports.get(port_id)
        if queue:
            asyncio.ensure_future(queue.put(frame))
        else:
            raise RuntimeError(f"Unknown port {port_id}")
