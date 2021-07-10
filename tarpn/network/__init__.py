import queue
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional, Tuple

from tarpn.datalink import L2Payload
from tarpn.log import LoggingMixin
#from tarpn.transport import L4Protocol


class QoS(IntEnum):
    Highest = 0
    Higher = 1
    Default = 2
    Lower = 3
    Lowest = 4


@dataclass(order=False, eq=True, frozen=True)
class L3Address:
    pass


@dataclass(order=True)
class L3Payload:
    source: L3Address = field(compare=False)  # TODO remove source and dest? should be encoded in the data
    destination: L3Address = field(compare=False)
    protocol: int = field(compare=False)
    buffer: bytes = field(compare=False)
    link_id: int = field(compare=False)
    qos: QoS = field(compare=True, default=QoS.Default)
    reliable: bool = field(compare=False, default=True)


class L3Queueing:
    def offer(self, packet: L3Payload) -> bool:
        raise NotImplementedError

    def maybe_take(self) -> Optional[L3Payload]:
        raise NotImplementedError


class L3PriorityQueue(L3Queueing):
    def __init__(self, max_size=20):
        self._queue = queue.PriorityQueue(max_size)

    def offer(self, packet: L3Payload):
        try:
            self._queue.put(packet, False, None)
            return True
        except queue.Full:
            print("Full!")
            return False

    def maybe_take(self) -> Optional[L3Payload]:
        try:
            return self._queue.get(True, 1.0)
        except queue.Empty:
            return None


class L3Protocol:
    def can_handle(self, protocol: int) -> bool:
        raise NotImplementedError

    def register_transport_protocol(self, protocol) -> None:
        raise NotImplementedError

    def handle_l2_payload(self, payload: L2Payload):
        raise NotImplementedError

    def route_packet(self, address: L3Address) -> Tuple[bool, int]:
        """
        Indicate if a address is route-able and return the L2 MTU for the link that
        will be used
        :param address:
        :return: a tuple of boolean and int, where the boolean is whether or not
                the address is route-able and the int is the MTU
        """
        raise NotImplementedError

    def send_packet(self, payload: L3Payload) -> bool:
        raise NotImplementedError

    def listen(self, address: L3Address):
        """
        When L4 wants to listen for connections at a certain address, it will call
        this method to inform L3 that it should accept packets for this destination
        and possibly include them in routing datastructures.
        """
        raise NotImplementedError

    def mtu(self) -> int:
        """
        The maximum packet size that can be transmitted with this network protocol
        """
        raise NotImplementedError


class L3Protocols(LoggingMixin):
    def __init__(self):
        LoggingMixin.__init__(self)
        self.protocols: List[L3Protocol] = []

    def register(self, protocol: L3Protocol):
        self.protocols.append(protocol)

    def handle_l2(self, payload: L2Payload):
        handled = False
        for protocol in self.protocols:
            # Pass the payload to any registered protocol that can handle it
            if protocol.can_handle(payload.l3_protocol):
                protocol.handle_l2_payload(payload)
                handled = True

        if not handled:
            self.debug(f"No L3 handler registered for protocol 0x{payload.l3_protocol:02X}, dropping")


class L3RoutingTable:
    def route1(self, destination: L3Address) -> Optional[int]:
        raise NotImplementedError
