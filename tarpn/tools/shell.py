import argparse
import asyncio
import signal
import sys
from asyncio import Protocol, Transport
from typing import Optional

from tarpn.util import shutdown


class TTY(Protocol):
    def __init__(self):
        self.transport: Optional[Transport] = None

    def connection_made(self, transport: Transport):
        self.transport = transport
        sys.stderr.write(f"Connected to {transport.get_extra_info('peername')}\r\n")
        sys.stderr.flush()

    def connection_lost(self, exc):
        sys.stderr.write(f"Lost connection to {self.transport.get_extra_info('peername')}\r\n")
        sys.stderr.flush()
        self.transport = None
        signal.raise_signal(signal.SIGTERM)

    def data_received(self, data: bytes) -> None:
        msg = data.decode("utf-8")
        for line in msg.splitlines():
            if line.startswith("Welcome to the TARPN shell") or line.startswith("(tarpn)"):
                return
            else:
                sys.stdout.write(line.strip())
                sys.stdout.flush()

    def handle_stdin(self):
        line = sys.stdin.readline()
        if self.transport is not None:
            if line == "":  # Got a ^D
                self.transport.close()
                signal.raise_signal(signal.SIGTERM)
            else:
                line = line.strip()
                self.transport.write(line + "\r\n")
        else:
            sys.stdout.write("Not connected\r\n")
            sys.stdout.flush()

    def handle_signal(self, loop, scheduler):
        if self.transport is not None:
            self.transport.close()
        asyncio.create_task(shutdown(loop))
        scheduler.shutdown()


async def async_main():
    parser = argparse.ArgumentParser(description='Open a shell to a running tarpn-core')
    parser.add_argument("sock", help="Path to unix socket")
    parser.add_argument("cmd", help="Command to send")
    args = parser.parse_args()

    tty = TTY()
    loop = asyncio.get_event_loop()
    loop.add_reader(sys.stdin, tty.handle_stdin)
    (transport, protocol) = await loop.create_unix_connection(lambda: tty, path=args.sock)
    transport.write(args.cmd.encode("utf-8"))
    await asyncio.sleep(1)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()