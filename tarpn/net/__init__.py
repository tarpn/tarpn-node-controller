import asyncio
from enum import Enum, auto
from typing import Optional

from tarpn.app import Application, Context, Echo
from tarpn.ax25 import AX25Call


class LoginState(Enum):
    Initial = auto()
    AwaitUser = auto()
    AwaitPwd = auto()
    Success = auto()
    Failure = auto()
    Closed = auto()


class LoginProtocol(asyncio.Protocol):
    def __init__(self, app: Application):
        self.app: Application = app
        self.transport = None
        self.client_host = None
        self.client_port = None
        self.state = LoginState.Initial
        self.user: Optional[AX25Call] = None

    def _context(self):
        return Context(self.transport.write, self.transport.close, self.user)

    def connection_made(self, transport):
        (host, port) = transport.get_extra_info('peername')
        print('Connection from {} {}'.format(host, port))
        self.transport = transport
        self.client_host = host
        self.client_port = port

    def data_received(self, data):
        if self.state == LoginState.Initial:
            msg = data.decode("ASCII").strip()
            if msg == "login":
                self.transport.write("user: ".encode("ASCII"))
                self.state = LoginState.AwaitUser
            else:
                self.transport.write("please enter 'login' to login.\r\n".encode("ASCII"))
        elif self.state == LoginState.AwaitUser:
            #  TODO validate callsign?
            msg = data.decode("ASCII").strip()
            self.user = AX25Call(msg, 0)
            self.transport.write("password: ".encode("ASCII"))
            self.state = LoginState.AwaitPwd
        elif self.state == LoginState.AwaitPwd:
            #  TODO check password
            self.app.on_connect(self._context())
            self.state = LoginState.Success
        elif self.state == LoginState.Success:
            self.app.read(self._context(), data)
        else:
            pass

    def eof_received(self):
        self.app.on_disconnect(self._context())
        self.state = LoginState.Closed


async def main():
    loop = asyncio.get_running_loop()
    server = await loop.create_server(lambda: LoginProtocol(Echo()), '127.0.0.1', 8888)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main(), debug=True)
