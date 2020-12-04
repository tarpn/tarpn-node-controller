import asyncio
import platform
import sys
from typing import Optional

from tarpn.app.runner import Context


class ChatApplication:
    """
    Two parts to this, the CHAT server and CHAT clients.

    The server is configured with a static list of neighbors to forward chat messages to.
    It will send periodic keep-alive messages to its neighbors with the current BPQChatServer
    version (6.0.14.12).

    CHAT clients can be any call sign, but only one instance of that call sign is supported
    on the network at once. When joining, the server will send out a "join" request for the
    user that connected.

       JK4DBZ-10 KM4NKU David from Python 3.7

    And once a topic is set, that will be send as well

       TK4DBZ-10 KM4NKU General



    Here are the client commands:
    02:39 PM: Commands can be in upper or lower case.
    02:39 PM: /U - Show Users.
    02:39 PM: /N - Enter your Name.
    02:39 PM: /Q - Enter your QTH.
    02:39 PM: /T - Show Topics.
    02:39 PM: /T Name - Join Topic or Create new Topic. Topic Names are not case sensitive
    02:39 PM: /P - Show Ports and Links.
    02:39 PM: /A - Toggle Alert on user join - Disabled.
    02:39 PM: /C - Toggle Colour Mode on or off (only works on Console or BPQTerminal - Disabled.
    02:39 PM: /Codepage CPnnnn - Set Codepage to use if UTF-9 is disabled.
    02:39 PM: /E - Toggle Echo - Enabled .
    02:39 PM: /Keepalive - Toggle sending Keepalive messages every 10 minutes - Disabled.
    02:39 PM: /ShowNames - Toggle displaying name as well as call on each message - Disabled
    02:39 PM: /Auto - Toggle Automatic character set selection - Disabled.
    02:39 PM: /UTF-8 - Character set Selection - UTF-8.
    02:39 PM: /Time - Toggle displaying timestamp on each message - Disabled.
    02:39 PM: /S CALL Text - Send Text to that station only.
    02:39 PM: /F - Force all links to be made.
    02:39 PM: /K - Show Known nodes.
    02:39 PM: /B - Leave Chat and return to node.
    02:39 PM: /QUIT - Leave Chat and disconnect from node.
    """
    keep_alive_thread: Optional = None

    def __init__(self, context: Context, environ, *args, **kwargs):
        self.context = context
        self.connected_chats = []
        self.keep_alive_chats = []
        asyncio.create_task(self._keep_alive())

    async def _keep_alive(self):
        await asyncio.sleep(3)  # Initial delay
        while True:
            for other_chat in self.keep_alive_chats:
                self.context.write(other_chat, b"\x01KK4DBZ-10 K4DBZ-9 6.0.14.12\r")
            await asyncio.sleep(60)

    def on_connect(self, address):
        print(f"CHAT got connection from {address}")
        self.connected_chats.append(address)

    def on_disconnect(self, address):
        print(f"CHAT got disconnected from {address}")
        self.connected_chats.remove(address)
        self.keep_alive_chats.remove(address)

    def on_data(self, address, data):
        print(f"CHAT got data from {address}: {repr(data)}")
        lines = data.split(b"\r")
        for line in lines:
            if len(line) == 0:
                continue
            if line == b"*RTL":
                # Remote station trying to connect, need to reply
                resp = b"[BPQChatServer-6.0.14.12]\rOK\r"
                self.context.write(address, resp)
                continue
            if line[0] == 1:
                inst = chr(line[1])
                rem = line[2:]
                if inst == "K":
                    print(f"CHAT keepalive")
                elif inst == "D":
                    msg = rem.decode("ASCII")
                    print(f"CHAT data: {msg}")
                elif inst == "J":
                    if rem.startswith(b"K4DBZ-9 K4DBZ"):
                        resp = b"\x01JK4DBZ-10 KM4NKU David from Python 3.7\r\x01TK4DBZ-10 KM4NKU General\r"
                        self.context.write(address, resp)
                        self.keep_alive_chats.append(address)
                    else:
                        msg = repr(rem)
                        print(f"CHAT join {msg}")
                elif inst == "S":
                    msg = rem.decode("ASCII")
                    print(f"Direct Message {msg}")
                    parts = msg.split(" ")
                    message_origin = parts[0]
                    message_user = parts[1]
                    message_target = parts[2]
                    message = " ".join(parts[3:])
                    if message == "version":
                        resp_msg = f"SK4DBZ-10 {message_target} {message_user} {sys.version}"
                    elif message == "os":
                        resp_msg = f"SK4DBZ-10 {message_target} {message_user} {platform.system()} " \
                                   f"{platform.machine()} {platform.release()}"
                    else:
                        resp_msg = f"SK4DBZ-10 {message_target} {message_user} Unknown command '{message}'"
                    self.context.write(address, b"\x01" + resp_msg.encode("ASCII") + b"\r")

                else:
                    msg = repr(rem)
                    print(f"CHAT unknown instruction {inst}: {msg}")
            else:
                msg = repr(line)
                print(f"CHAT unknown: {msg}")
