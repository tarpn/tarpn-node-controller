import unittest

from tarpn.util import Sequence


class TestUtils(unittest.TestCase):
    def test_sequence(self):
        m = Sequence(0)
        n = Sequence(0)
        print(n-m)

        m = Sequence(0)
        n = Sequence(32767)
        print(n-m)

