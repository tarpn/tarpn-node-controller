import asyncio
import logging
from typing import List, cast

from tarpn.ax25 import AX25Call, L3Protocol, decode_ax25_packet, AX25Packet, AX25, AX25StateType
from tarpn.ax25 import UIFrame, L3Handler
from tarpn.port import PortFrame
from tarpn.ax25.statemachine import AX25StateMachine, AX25StateEvent
from tarpn.events import EventBus

logger = logging.getLogger("ax25.datalink")
packet_logger = logging.getLogger("packet")


class DataLinkManager(AX25):
    """
    In the AX.25 spec, this is the Link Multiplexer. It accepts packets from a single physical device and
    manages Data Links for each connection. Packets are sent here via a provided queue. As packets are
    processed, they may generate outgoing packets or L3 network events (such as DL_CONNECT).

    L2 applications may be bound to this class. This allows for simple point-to-point connected applications
    such as SYSOP.

    L3 handlers may be bound to this class. This allows for passing L3 network events to higher layers. These
    handlers are run in the order that they are added to this class.
    """
    def __init__(self,
                 link_call: AX25Call,
                 link_port: int,
                 inbound: asyncio.Queue,
                 outbound: asyncio.Queue):
        self.link_call = link_call
        self.link_port = link_port
        self.inbound = inbound      # PortFrame
        self.outbound = outbound    # PortFrame
        self.state_machine = AX25StateMachine(self)
        self.l3_handlers: List[L3Handler] = []
        self._stopped: bool = False

    async def start(self):
        logger.info("Start DataLinkManager")
        while not self._stopped:
            await self._loop()

    def stop(self):
        self._stopped = True

    async def _loop(self):
        frame = await self.inbound.get()
        if frame:
            self._loop_sync(frame)
            self.inbound.task_done()

    def _loop_sync(self, frame: PortFrame):
        try:
            packet = decode_ax25_packet(frame.data)
            packet_logger.info(f"L2 RX: {packet}")
            EventBus.emit("packet", [packet])
        except Exception:
            logger.exception(f"Had an error parsing packet: {frame}")
            return

        try:
            # Check if this is a special L3 message
            should_continue = True
            for l3 in self.l3_handlers:
                should_continue = l3.maybe_handle_special(frame.port, packet)
                if not should_continue:
                    logging.debug(f"Handled by L3 {l3}")
                    break

            # If it has not been handled by L3
            if should_continue:
                if not packet.dest == self.link_call:
                    logger.warning(f"Discarding packet not for us {packet}. We are {self.link_call}")
                else:
                    self.state_machine.handle_packet(packet)
            else:
                logger.debug("Not continuing because this packet was handled by L3")
        except Exception:
            logger.exception(f"Had handling packet {packet}")

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

    def add_l3_handler(self, l3_handler: L3Handler):
        self.l3_handlers.append(l3_handler)

    def dl_error(self, remote_call: AX25Call, local_call: AX25Call, error_code: str):
        EventBus.emit(f"link.{local_call}.error", error_code)

    def dl_connect_request(self, remote_call: AX25Call):
        if remote_call == self.link_call:
            raise RuntimeError(f"Cannot connect to node's own callsign {remote_call}")
        dl_connect = AX25StateEvent.dl_connect(remote_call, self.link_call)
        self.state_machine.handle_internal_event(dl_connect)

    def dl_connect_indication(self, remote_call: AX25Call, local_call: AX25Call):
        EventBus.emit(f"link.{local_call}.connect", remote_call)

    def dl_disconnect_request(self, remote_call: AX25Call):
        dl_disconnect = AX25StateEvent.dl_disconnect(remote_call, self.link_call)
        self.state_machine.handle_internal_event(dl_disconnect)

    def dl_disconnect_indication(self, remote_call: AX25Call, local_call: AX25Call):
        EventBus.emit(f"link.{local_call}.disconnect", remote_call)

    def dl_data_request(self, remote_call: AX25Call, protocol: L3Protocol, data: bytes):
        event = AX25StateEvent.dl_data(remote_call, protocol, data)
        self.state_machine.handle_internal_event(event)
        return event.future

    def dl_data_indication(self, remote_call: AX25Call, local_call: AX25Call, protocol: L3Protocol, data: bytes):
        EventBus.emit(f"link.{local_call}.inbound", remote_call, protocol, data)
        handled = False
        for l3 in self.l3_handlers:
            if l3.can_handle(protocol):
                handled = l3.handle(self.link_port, remote_call, data)
            if handled:
                break

        if not handled:
            logger.warning(f"No handler defined for protocol {repr(protocol)}. Discarding")

    def write_packet(self, packet: AX25Packet):
        packet_logger.info(f"L2 TX: {packet}")
        frame = PortFrame(self.link_port, packet.buffer, 0)
        asyncio.create_task(self.outbound.put(frame))

    def link_state(self, remote_call: AX25Call) -> AX25StateType:
        return self.state_machine.get_state(remote_call)

    def callsign(self):
        return self.link_call


class IdHandler(L3Handler):
    """Handle a few special packet types"""

    def can_handle(self, protocol: L3Protocol) -> bool:
        return protocol == L3Protocol.NoLayer3

    def maybe_handle_special(self, port: int, packet: AX25Packet) -> bool:
        if packet.dest == AX25Call("ID") and isinstance(packet, UIFrame):
            ui = cast(UIFrame, packet)
            print(f"Got ID from {packet.source}: {ui.info}")
            return False
        elif packet.dest == AX25Call("CQ") and isinstance(packet, UIFrame):
            ui = cast(UIFrame, packet)
            print(f"Got CQ from {packet.source}: {ui.info}")
            return False
        return True