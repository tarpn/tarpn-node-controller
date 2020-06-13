import asyncio
from typing import Dict

from tarpn.app import Application, Context
from tarpn.ax25 import AX25Call, L3Protocol, decode_ax25_packet, AX25Packet, AX25
from tarpn.ax25.statemachine import AX25StateMachine, AX25StateEvent
from tarpn.frame import L3Handler, DataLinkMultiplexer, DataLinkFrame


class DataLink(AX25):
    """
    This accepts packets from one or more ports via the given queue. These are passed through
    an AX.25 state machine which then emits outgoing packets or network events (such as DL_Connect).

    This class also supports binding l2 applications and l3 handlers.
    """
    def __init__(self,
                 inbound: asyncio.Queue,
                 dlm: DataLinkMultiplexer,
                 default_app: Application):
        """
        AX25 data-link layer
        """
        self.inbound = inbound
        self.dlm = dlm
        self.state_machine = AX25StateMachine(self)
        self.last_seen_on_port: Dict[AX25Call, int] = {}
        self.l2_apps: Dict[AX25Call, Application] = {}
        self.default_app: Application = default_app
        self.l3: Dict[L3Protocol, L3Handler] = {}

    async def start(self):
        while True:
            await self._loop()

    async def _loop(self):
        frame = await self.inbound.get()
        if frame:
            packet = decode_ax25_packet(frame.data)
            self.last_seen_on_port[packet.source] = frame.port

            # Check if this is a special L3 message
            l3_handled = False
            for l3 in self.l3.values():
                l3_handled = l3.maybe_handle_special(packet)
                if l3_handled:
                    break

            # If it has not been handled by L3
            if not l3_handled:
                self.state_machine.handle_packet(packet)
            self.inbound.task_done()

    def _writer_partial(self, remote_call: AX25Call):
        def inner(data: bytes):
            self.state_machine.handle_internal_event(
                AX25StateEvent.dl_data(remote_call, L3Protocol.NoLayer3, data))
        return inner

    def _closer_partial(self, remote_call: AX25Call, local_call: AX25Call):
        def inner():
            self.state_machine.handle_internal_event(
                AX25StateEvent.dl_disconnect(remote_call, local_call))
        return inner

    def bind_l2_application(self, local_call: AX25Call, app: Application):
        # TODO change this to take in an app factory
        self.l2_apps[local_call] = app

    def add_l3_handler(self, protocol: L3Protocol, l3_handler: L3Handler):
        self.l3[protocol] = l3_handler

    def dl_error(self, remote_call: AX25Call, local_call: AX25Call, error_code: str):
        # print(f"Got DL error {error_code}")
        app = self.l2_apps.get(local_call, self.default_app)
        context = Context(
            self._writer_partial(remote_call),
            self._closer_partial(remote_call, local_call),
            remote_call
        )
        app.on_error(context, error_code)

    def dl_connect(self, remote_call: AX25Call, local_call: AX25Call):
        # print(f"Connected to {remote_call}")
        app = self.l2_apps.get(local_call, self.default_app)
        context = Context(
            self._writer_partial(remote_call),
            self._closer_partial(remote_call, local_call),
            remote_call
        )
        app.on_connect(context)

    def dl_disconnect(self, remote_call: AX25Call, local_call: AX25Call):
        # print(f"Disconnected from {remote_call}")
        app = self.l2_apps.get(local_call, self.default_app)
        context = Context(
            self._writer_partial(remote_call),
            self._closer_partial(remote_call, local_call),
            remote_call
        )
        app.on_disconnect(context)

    def dl_data(self, remote_call: AX25Call, local_call: AX25Call, protocol: L3Protocol, data: bytes):
        # print(f"Got DL Data ({protocol}: {data}")
        app = self.l2_apps.get(local_call, self.default_app)
        context = Context(
            self._writer_partial(remote_call),
            self._closer_partial(remote_call, local_call),
            remote_call
        )
        app.read(context, data)

    def write_packet(self, packet: AX25Packet):
        asyncio.ensure_future(self._async_write(packet))

    async def _async_write(self, packet: AX25Packet):
        port = self.last_seen_on_port[packet.dest]
        frame = DataLinkFrame(port, packet.buffer, 0)
        self.dlm.write_to_port(port, frame)
