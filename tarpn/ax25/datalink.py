import asyncio
from typing import Dict

from tarpn.app import Application, Context
from tarpn.ax25 import AX25Call, L3Protocol, decode_ax25_packet, AX25Packet, AX25
from tarpn.ax25.statemachine import AX25StateMachine, AX25StateEvent
from tarpn.frame import L3Handler, DataLinkFrame


class DataLink(AX25):
    """
    This accepts packets from one or more ports via the given queue. These are passed through
    an AX.25 state machine which then emits outgoing packets or network events (such as DL_Connect).

    This class also supports binding l2 applications and l3 handlers.
    """
    def __init__(self,
                 link_call: AX25Call,
                 link_port: int,
                 inbound: asyncio.Queue,
                 outbound: asyncio.Queue,
                 default_app: Application):
        """
        AX25 data-link layer
        """
        self.link_call = link_call
        self.link_port = link_port
        self.inbound = inbound
        self.outbound = outbound
        self.state_machine = AX25StateMachine(self)
        self.default_app: Application = default_app
        self.l3: Dict[L3Protocol, L3Handler] = {}

    async def start(self):
        while True:
            await self._loop()

    async def _loop(self):
        frame = await self.inbound.get()
        if frame:
            try:
                packet = decode_ax25_packet(frame.data)

                # Check if this is a special L3 message
                should_continue = True
                for l3 in self.l3.values():
                    should_continue = l3.maybe_handle_special(packet)
                    if not should_continue:
                        break

                # If it has not been handled by L3
                if should_continue:
                    if not packet.dest == self.link_call:
                        print(f"Discarding packet not for us {packet}. We are {self.link_call}")
                        return
                    self.state_machine.handle_packet(packet)
            finally:
                self.inbound.task_done()

    def _l3_writer_partial(self, remote_call: AX25Call, protocol: L3Protocol):
        def inner(data: bytes):
            self.state_machine.handle_internal_event(
                AX25StateEvent.dl_data(remote_call, protocol, data))
        return inner

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

    def add_l3_handler(self, protocol: L3Protocol, l3_handler: L3Handler):
        self.l3[protocol] = l3_handler

    def dl_error(self, remote_call: AX25Call, local_call: AX25Call, error_code: str):
        context = Context(
            self._writer_partial(remote_call),
            self._closer_partial(remote_call, local_call),
            remote_call
        )
        self.default_app.on_error(context, error_code)

    def dl_connect(self, remote_call: AX25Call, local_call: AX25Call):
        context = Context(
            self._writer_partial(remote_call),
            self._closer_partial(remote_call, local_call),
            remote_call
        )
        self.default_app.on_connect(context)

    def dl_disconnect(self, remote_call: AX25Call, local_call: AX25Call):
        context = Context(
            self._writer_partial(remote_call),
            self._closer_partial(remote_call, local_call),
            remote_call
        )
        self.default_app.on_disconnect(context)

    def dl_data(self, remote_call: AX25Call, local_call: AX25Call, protocol: L3Protocol, data: bytes):
        if protocol in (L3Protocol.NoLayer3, L3Protocol.NoProtocol):
            # If no protocol defined, handle with the default L2 app
            context = Context(
                self._writer_partial(remote_call),
                self._closer_partial(remote_call, local_call),
                remote_call
            )
            self.default_app.read(context, data)
        else:
            l3 = self.l3.get(protocol)
            if l3:
                l3.handle(self.link_port, remote_call, data)
            else:
                print(f"No handler defined for protocol {protocol}. Discarding")

    def write_packet(self, packet: AX25Packet):
        print(f"Sending out {packet}")
        frame = DataLinkFrame(self.link_port, packet.buffer, 0)
        asyncio.create_task(self.outbound.put(frame))
