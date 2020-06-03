from dataclasses import dataclass
from typing import Callable


@dataclass
class DataLinkFrame:
    port: int
    data: bytes
    hldc_port: int
    write_callback: Callable[[bytes], None]
