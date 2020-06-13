from typing import Callable

from tarpn.ax25 import AX25Call


class Context:
    def __init__(self, writer: Callable[[bytes], None], closer: Callable[[], None], remote_call: AX25Call):
        self.writer = writer
        self.closer = closer
        self.remote_call = remote_call

    def write(self, data: bytes):
        self.writer(data)

    def close(self):
        self.closer()

    def remote_address(self) -> str:
        return str(self.remote_call)


class Application:
    def on_connect(self, context: Context):
        pass

    def on_disconnect(self, context: Context):
        pass

    def on_error(self, context: Context, error: str):
        pass

    def read(self, context: Context, data: bytes):
        pass


class Logger(Application):
    def on_connect(self, context: Context):
        print(f"Connected to {context.remote_address()}")

    def on_disconnect(self, context: Context):
        print(f"Disconnected from {context.remote_address()}")

    def on_error(self, context: Context, error: str):
        print(f"Error: {error}")

    def read(self, context: Context, data: bytes):
        print("Got: " + data.decode("ASCII"))


class Echo(Application):
    def on_connect(self, context: Context):
        print(f"ECHO Connected to {context.remote_address()}")

    def on_disconnect(self, context: Context):
        print(f"ECHO Disconnected from {context.remote_address()}")

    def on_error(self, context: Context, error: str):
        print(f"ECHO Error: {error}")

    def read(self, context: Context, data: bytes):
        print("ECHO Got: " + data.decode("ASCII"))
        context.write(data)
