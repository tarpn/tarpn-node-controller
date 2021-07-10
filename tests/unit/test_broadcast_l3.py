import unittest

from tarpn.network.mesh.header import DatagramHeader


class TestBroadcast(unittest.TestCase):
    def test_fragment(self):
        msg = """
Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore 
et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut 
aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum 
dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui 
officia deserunt mollit anim id est laborum""".replace("\r", "").encode("utf-8")
        datagram = DatagramHeader(100, 100, len(msg), 0)
        # TODO fix this
