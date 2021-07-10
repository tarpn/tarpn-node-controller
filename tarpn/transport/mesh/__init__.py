from io import BytesIO
from typing import Dict

from tarpn.log import LoggingMixin
from tarpn.network.mesh.header import NetworkHeader, Protocol


class L4Handler:
    def handle_l4(self, network_header: NetworkHeader, stream: BytesIO):
        raise NotImplementedError


class L4Handlers(LoggingMixin):
    def __init__(self):
        LoggingMixin.__init__(self)
        self.handlers: Dict[Protocol, L4Handler] = dict()

    def register_l4(self, protocol: Protocol, handler: L4Handler):
        if protocol not in self.handlers:
            self.handlers[protocol] = handler
        else:
            raise RuntimeError(f"Already have handler registered for {protocol}")

    def handle_l4(self, network_header: NetworkHeader, protocol: Protocol, stream: BytesIO):
        if protocol in self.handlers:
            self.handlers[protocol].handle_l4(network_header, stream)
        else:
            self.warning(f"Dropping unsupported protocol {protocol}")
