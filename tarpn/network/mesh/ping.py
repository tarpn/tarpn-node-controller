import dataclasses
import secrets
import struct
import threading
import time
from collections import deque, defaultdict
from functools import partial
from io import BytesIO
from typing import Dict, Optional

from tarpn.log import LoggingMixin
from tarpn.network import QoS
from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.header import ControlHeader, ControlType, NetworkHeader, Protocol
from tarpn.util import secure_random_byte, secure_random_data


@dataclasses.dataclass
class PingResult:
    seq: int
    start_ns: int
    end_ns: Optional[int]
    cond: threading.Condition


@dataclasses.dataclass
class PingStats:
    results: deque = dataclasses.field(default_factory=partial(deque, maxlen=50))

    def get(self, seq) -> Optional[PingResult]:
        for result in reversed(self.results):
            if result.seq == seq:
                return result
        return None


class PingProtocol(LoggingMixin):
    def __init__(self, network):
        self.network = network
        self.mutex = threading.Lock()
        self.stats: Dict[MeshAddress, PingStats] = defaultdict(PingStats)
        LoggingMixin.__init__(self)

    def send_ping(self, node: MeshAddress, size: int = 1) -> int:
        ctrl = ControlHeader(True, ControlType.PING, size, secure_random_data(size))
        network_header = NetworkHeader(
            version=0,
            qos=QoS.Higher,
            protocol=Protocol.CONTROL,
            ttl=7,
            identity=self.network.next_sequence(),
            length=0,
            source=self.network.our_address,
            destination=node,
        )
        stream = BytesIO()
        network_header.encode(stream)
        ctrl.encode(stream)
        stream.seek(0)
        buffer = stream.read()
        self.stats[node].results.append(
            PingResult(ctrl.extra[0], time.time_ns(), None, threading.Condition()))
        self.network.send(network_header, buffer)
        return ctrl.extra[0]

    def handle_ping(self, network_header: NetworkHeader, ctrl: ControlHeader):
        if ctrl.is_request:
            network_resp = dataclasses.replace(network_header,
                                               ttl=7,
                                               identity=self.network.next_sequence(),
                                               source=self.network.our_address,
                                               destination=network_header.source)
            ctrl_resp = dataclasses.replace(ctrl, is_request=False)
            stream = BytesIO()
            network_resp.encode(stream)
            ctrl_resp.encode(stream)
            stream.seek(0)
            buffer = stream.read()
            self.network.send(network_resp, buffer)
        else:
            now = time.time_ns()
            result = self.stats.get(network_header.source).get(ctrl.extra[0])
            if result is not None:
                self.debug(f"Got ping response {ctrl}")
                result.end_ns = now
                with result.cond:
                    result.cond.notify_all()
            else:
                self.warning(f"Ignoring unexpected ping response {ctrl}")

    def wait_for_ping(self, node: MeshAddress, seq: int, timeout_ms: int) -> Optional[PingResult]:
        result = self.stats.get(node).get(seq)
        if result is not None:
            with result.cond:
                done = result.cond.wait(timeout_ms / 1000.)
                if done:
                    return result
                else:
                    self.warning(f"Timed out waiting for ping {seq} from {node} after {timeout_ms}ms")
                    return None
        else:
            self.warning(f"No ping from {node} found with seq {seq}")
            return None
