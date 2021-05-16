import datetime
import logging
import signal
import sys

from tarpn.app.runner import NetworkApp, Context, AppPlugin
from tarpn.log import LoggingMixin

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


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
