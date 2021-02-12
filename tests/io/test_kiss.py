import unittest
from typing import Optional, List

from tarpn.datalink import L2IOQueuing, FrameData
from tarpn.io.kiss import KISSProtocol


class ListQueue(L2IOQueuing, List[FrameData]):
    def __init__(self):
        super().__init__()

    def take_outbound(self) -> Optional[FrameData]:
        raise NotImplementedError()

    def offer_inbound(self, frame: FrameData) -> None:
        self.append(frame)


class TestKiss(unittest.TestCase):
    def test_basic(self):
        queue = ListQueue()
        kiss = KISSProtocol(0, queue)
        kiss.handle_bytes(b"\xc0\x00Hello, KISS!\xc0")
        assert len(queue) == 1
        assert queue.pop(0).data == b"Hello, KISS!"

    def test_streaming(self):
        queue = ListQueue()
        kiss = KISSProtocol(0, queue)
        kiss.handle_bytes(b"\xc0\x00Hello,")
        assert len(queue) == 0
        kiss.handle_bytes(b"")
        kiss.handle_bytes(b" KISS!\xc0")
        assert len(queue) == 1
        assert queue.pop(0).data == b"Hello, KISS!"

    def test_partial(self):
        queue = ListQueue()
        kiss = KISSProtocol(0, queue)
        kiss.handle_bytes(b"\xb4@\x04_\x96h\x88\x84\xb4@\x12CHAT  \x96h\x88\x84\xb4@\x04_\xc0")
        assert len(queue) == 0
        kiss.handle_bytes(b"\xc0\x00Hello, KISS!\xc0")
        assert len(queue) == 1
        assert queue.pop(0).data == b"Hello, KISS!"
