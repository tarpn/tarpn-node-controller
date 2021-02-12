import logging
from copy import copy
from dataclasses import dataclass, field
from typing import Dict

from tarpn.ax25 import decode_ax25_packet, AX25Call, AX25, AX25Packet, L3Protocol, AX25StateType
from tarpn.ax25.statemachine import AX25StateMachine, AX25StateEvent
from tarpn.datalink import L2Queuing, FrameData, L2Address, L2Payload
from tarpn.datalink.protocol import L2Protocol, LinkMultiplexer
from tarpn.network import L3Protocols, L3Payload
from tarpn.log import LoggingMixin
from tarpn.scheduler import Scheduler
from tarpn.util import chunks


@dataclass
class AX25Address(L2Address):
    callsign: str = field()
    ssid: int = field(default=0)

    def __repr__(self):
        return f"AX25Address({self.callsign}-{self.ssid})"

    def to_ax25_call(self):
        return AX25Call(self.callsign, self.ssid)

    @classmethod
    def from_ax25_call(cls, ax25_call: AX25Call):
        if ax25_call is not None:
            return cls(ax25_call.callsign, ax25_call.ssid)
        else:
            return None


class AX25Protocol(L2Protocol, AX25, LoggingMixin):
    def __init__(self, link_port: int, link_call: AX25Call, scheduler: Scheduler, queue: L2Queuing,
                 link_multiplexer: LinkMultiplexer, l3_protocols: L3Protocols):
        self.link_port = link_port
        self.link_call = link_call
        self.queue = queue  # l2 buffer
        self.link_multiplexer = link_multiplexer
        self.l3_protocols = l3_protocols

        # Mapping of link_id to AX25 address. When we establish new logical links,
        # add them here so we can properly address payloads from L3
        self.links_by_id: Dict[int, AX25Call] = dict()
        self.links_by_address: Dict[AX25Call, int] = dict()

        self.state_machine = AX25StateMachine(self, scheduler.timer)
        self.link_multiplexer.register_device(self)

        def extra():
            return f"[L2 AX25 Port={self.link_port} Call={str(self.link_call)}]"
        LoggingMixin.__init__(self, logging.getLogger("main"), extra)

    @classmethod
    def maximum_transmission_unit(cls) -> int:
        """I and UI frames have 16 header bytes"""
        return 240

    @classmethod
    def maximum_frame_size(cls) -> int:
        """AX.25 packets are limited to 256 bytes per the spec"""
        return 256

    def get_device_id(self) -> int:
        return self.link_port

    def get_link_address(self) -> L2Address:
        return AX25Address.from_ax25_call(self.link_call)

    def get_peer_address(self, link_id) -> L2Address:
        return AX25Address.from_ax25_call(self.links_by_id.get(link_id))

    def peer_connected(self, link_id) -> bool:
        remote_call = self.links_by_id.get(link_id)
        return self.state_machine.get_state(remote_call) in (AX25StateType.Connected, AX25StateType.TimerRecovery)

    def receive_frame(self, frame: FrameData):
        try:
            ax25_packet = decode_ax25_packet(frame.data)
            self.maybe_create_logical_link(ax25_packet.source)
            if ax25_packet.dest == AX25Call("NODES"):
                self.debug(f"RX: {ax25_packet}")
                # Eagerly connect to neighbors sending NODES
                if self.state_machine.get_state(ax25_packet.source) in (AX25StateType.Disconnected,
                                                                        AX25StateType.AwaitingRelease):
                    self.dl_connect_request(copy(ax25_packet.source))
            else:
                self.debug(f"RX: {ax25_packet}")
        except Exception:
            self.exception(f"Had error parsing packet: {frame}")
            return

        try:
            self.state_machine.handle_packet(ax25_packet)
        except Exception:
            self.exception(f"Had error handling packet {ax25_packet}")

    def handle_queue_full(self):
        self.warning("L2 inbound queue is full!")

    def send_packet(self, payload: L3Payload) -> bool:
        remote_call = self.links_by_id.get(payload.link_id)
        if remote_call is None:
            self.warning(f"No logical link has been established with id {payload.link_id}, dropping.")
            return True

        if self.state_machine.is_window_exceeded(remote_call):
            return False

        if len(payload.buffer) > self.maximum_transmission_unit():
            self.warning("Fragmenting L3 payload for L2 MTU!!")

        for chunk in chunks(payload.buffer, self.maximum_transmission_unit()):
            if payload.reliable:
                event = AX25StateEvent.dl_data(remote_call, L3Protocol(payload.protocol), chunk)
            else:
                event = AX25StateEvent.dl_unit_data(remote_call, L3Protocol(payload.protocol), chunk)
            try:
                self.state_machine.handle_internal_event(event)
            except RuntimeError:
                self.exception(f"Error processing state event {event} for {remote_call}, dropping.")
            finally:
                return True

    def dl_error(self, remote_call: AX25Call, local_call: AX25Call, error_code: str):
        self.debug(f"Got data-link error {error_code}: {self.error_message(error_code)} on link to {remote_call}")

    def dl_data_request(self, remote_call: AX25Call, protocol: L3Protocol, data: bytes):
        return super().dl_data_request(remote_call, protocol, data)

    def dl_data_indication(self, remote_call: AX25Call, local_call: AX25Call, protocol: L3Protocol, data: bytes):
        link_id = self.links_by_address.get(remote_call)
        if link_id is not None:
            pdu = L2Payload(link_id, AX25Address.from_ax25_call(remote_call),
                            AX25Address.from_ax25_call(local_call), protocol, data)
            self.l3_protocols.handle_l2(pdu)
        else:
            self.warning(f"No logical link has been established for {remote_call}, dropping.")

    def dl_connect_request(self, remote_call: AX25Call):
        dl_connect = AX25StateEvent.dl_connect(remote_call, self.link_call)
        self.state_machine.handle_internal_event(dl_connect)

    def dl_connect_indication(self, remote_call: AX25Call, local_call: AX25Call):
        self.info(f"Connection made on link to {remote_call}")

    def dl_disconnect_request(self, remote_call: AX25Call):
        return super().dl_disconnect_request(remote_call)

    def dl_disconnect_indication(self, remote_call: AX25Call, local_call: AX25Call):
        self.info(f"Connection lost on link to {remote_call}")

    def write_packet(self, packet: AX25Packet):
        frame = FrameData(self.link_port, packet.buffer)
        self.debug(f"TX: {packet}")
        if not self.queue.offer_outbound(frame):
            self.warning("Could not send frame, buffer full")

    def local_call(self) -> AX25Call:
        return self.link_call

    def maybe_create_logical_link(self, remote_call: AX25Call) -> int:
        link_id = self.links_by_address.get(remote_call)
        if link_id is None:
            link_id = self.link_multiplexer.add_link(self)
            self.links_by_address[remote_call] = link_id
            self.links_by_id[link_id] = remote_call
        return link_id
