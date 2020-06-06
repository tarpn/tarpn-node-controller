import asyncio
from dataclasses import dataclass
from typing import Callable, Dict


@dataclass
class DataLinkFrame:
    port: int
    data: bytes
    hldc_port: int
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
