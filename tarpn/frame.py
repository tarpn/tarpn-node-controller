from typing import Callable, NamedTuple


class Frame(NamedTuple):
    port: int
    data: bytes
    write_callback: Callable[[bytes], None]
