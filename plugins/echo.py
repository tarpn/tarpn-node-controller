import logging
import sys

from tarpn.app.runner import NetworkApp, Context, AppPlugin
from tarpn.log import LoggingMixin

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


class Echo(NetworkApp, LoggingMixin):
    def __init__(self, environ):
        NetworkApp.__init__(self, environ)
        LoggingMixin.__init__(self)
        self.context = None

    def on_network_connect(self, address: str):
        self.debug(f"Network connect to {address}")

    def on_network_data(self, address: str, data: bytes):
        print(f"Network data from {address}: {data}")
        s = data.decode("utf-8").strip() + "\r\n"
        if self.context is not None:
            self.context.write("ff.ff", f"{address} said: {s}".encode("utf-8"))

    def on_network_disconnect(self, address: str):
        print(f"Network disconnect from {address}")

    def connection_made(self, context: Context):
        print(f"Socket connected")
        self.context = context

    def connection_lost(self):
        print(f"Socket disconnected")


class EchoPlugin(AppPlugin):
    def network_app(self, *args) -> NetworkApp:
        return Echo(environ={})