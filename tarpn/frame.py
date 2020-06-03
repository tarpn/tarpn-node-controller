from dataclasses import dataclass
from typing import Callable


@dataclass
class DataLinkFrame:
    port: int
    data: bytes
    write_callback: Callable[[bytes], None]
