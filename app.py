#!/usr/bin/env python
import datetime
import logging
import signal
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


class TTY(NetworkApp, LoggingMixin):
    def __init__(self, environ):
        NetworkApp.__init__(self, environ)
        LoggingMixin.__init__(self)
        self.context = None

    def connection_made(self, context: Context):
        self.context = context

    def connection_lost(self):
        self.context = None

    def on_network_data(self, address: str, data: bytes):
        msg = data.decode("utf-8").strip()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        out = f"{now} -- {address} -- {msg}\r\n"
        sys.stdout.write(out)

    def handle_stdin(self):
        line = sys.stdin.readline()
        if self.context is not None:
            if line == "":  # Got a ^D
                self.context.close()
                signal.raise_signal(signal.SIGTERM)
            else:
                line = line.strip()
                self.context.write("ff.ff", f"{line}\r\n".encode("utf-8"))
        else:
            sys.stdout.write("Not connected\r\n")
            sys.stdout.flush()


class TTYAppPlugin(AppPlugin):
    def network_app(self, loop) -> NetworkApp:
        tty = TTY(environ={})
        loop.add_reader(sys.stdin, tty.handle_stdin)
        return tty
