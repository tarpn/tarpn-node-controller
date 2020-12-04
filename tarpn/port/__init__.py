from dataclasses import dataclass
import logging
from typing import Callable

logger = logging.getLogger("port")


@dataclass
class PortFrame:
    port: int
    data: bytes
    hldc_port: int = 0
    write_callback: Callable[[bytes], None] = lambda _: None
