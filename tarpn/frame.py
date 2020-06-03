from dataclasses import dataclass
from typing import Callable


@dataclass
class DataLinkFrame:
    port: int
    data: bytes
    hldc_port: int
    write_callback: Callable[[bytes], None] = lambda _: None


class DataLinkMultiplexer:
    def __init__(self):
        self.ports = {}

    def add_port(self, port_id: int, port_cb: Callable[[DataLinkFrame], None]):
        self.ports[port_id] = port_cb

    def get_port(self, port_id: int):
        return self.ports.get(port_id)