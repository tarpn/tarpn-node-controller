class IODevice:
    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def start(self) -> None:
        raise NotImplementedError


class IOProtocol:
    def handle_bytes(self, data: bytes) -> None:
        raise NotImplementedError

    def next_bytes_to_write(self) -> bytes:
        raise NotImplementedError


