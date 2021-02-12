from typing import Tuple

from tarpn.ax25 import AX25Call
from tarpn.datalink import L2Payload
from tarpn.datalink.ax25_l2 import AX25Address
from tarpn.network import L3Protocol, L3Payload, L3Address
from tarpn.transport import L4Protocol


class NoLayer3Protocol(L3Protocol):
    """
    Special L3 handler for "NoLayer3"
    """

    def register_transport_protocol(self, protocol: L4Protocol) -> None:
        pass

    def route_packet(self, payload: L3Payload) -> Tuple[bool, int]:
        return True, 0

    def send_packet(self, payload: L3Payload) -> bool:
        pass

    def listen(self, address: L3Address):
        pass

    def can_handle(self, protocol: int) -> bool:
        return protocol == 0xF0

    def handle_l2_payload(self, payload: L2Payload):
        if payload.destination == AX25Address.from_ax25_call(AX25Call("ID")):
            print(f"Got ID from {payload.source}: {payload.l3_data}")
        elif payload.destination == AX25Address.from_ax25_call(AX25Call("CQ")):
            print(f"Got CQ from {payload.source}: {payload.l3_data}")
        else:
            print(f"Unhandled NoLayer3 payload {payload}, dropping.")