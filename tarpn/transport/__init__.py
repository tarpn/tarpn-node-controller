from typing import Dict, Any, List


class L4Address:
    pass


class L4Protocol:
    pass


class Transport:
    def __init__(self, extra=None):
        if extra is None:
            extra = {}
        self._extra = extra

    def get_extra_info(self, name, default=None) -> Dict[str, Any]:
        """Get optional transport information."""
        return self._extra.get(name, default)

    def is_closing(self) -> bool:
        """Return True if the transport is closing or closed."""
        raise NotImplementedError

    def close(self) -> None:
        """Close the transport.

        Buffered data will be flushed asynchronously.  No more data
        will be received.  After all buffered data is flushed, the
        protocol's connection_lost() method will (eventually) be
        called with None as its argument.
        """
        raise NotImplementedError

    def write(self, data: Any) -> None:
        """Write some data bytes to the transport.

        This does not block; it buffers the data and arranges for it
        to be sent out asynchronously.
        """
        raise NotImplementedError

    def writeline(self, line: str) -> None:
        self.write(line + "\r\n")

    def writelines(self, lines: List[str]) -> None:
        self.write("\r\n".join(lines) + "\r\n")

    def get_write_buffer_size(self) -> int:
        """Return the current size of the write buffer."""
        raise NotImplementedError


class Protocol:
    def connection_made(self, transport: Transport):
        """Called when a connection is made.

        The argument is the transport representing the pipe connection.
        To receive data, wait for data_received() calls.
        When the connection is closed, connection_lost() is called.
        """

    def connection_lost(self, exc):
        """Called when the connection is lost or closed.

        The argument is an exception object or None (the latter
        meaning a regular EOF is received or the connection was
        aborted or closed).
        """

    def data_received(self, data: bytes):
        """Called when some data is received.

        The argument is a bytes object.
        """