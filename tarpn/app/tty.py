import asyncio

from tarpn.app import Application, Context


class TTY(Application):
    """Simple keyboard-to-keyboard text application"""

    def start(self):
        loop = asyncio.get_running_loop()
        server = await loop.create_server(lambda: LoginProtocol(Echo()), '127.0.0.1', 8888)

    def on_connect(self, context: Context):
        print("connect")

    def on_disconnect(self, context: Context):
        print("disconnect")

    def on_error(self, context: Context, error: str):
        print("error")

    def read(self, context: Context, data: bytes):
        print(data)