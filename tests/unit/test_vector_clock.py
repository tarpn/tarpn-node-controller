import unittest

from tarpn.util import VectorClock, MutableVectorClock


class VectorClockTest(unittest.TestCase):
    def test_monotonic(self):
        vc = MutableVectorClock(*[0])
        vc.inc_timestamp(0)
        self.assertEqual(vc[0], 1)
        self.assertRaises(ValueError, vc.set_timestamp, 0, 0)
        self.assertEqual(vc[0], 1)
        vc.inc_timestamp(0)
        self.assertEqual(vc[0], 2)
        vc.set_timestamp(0, 2)
        self.assertEqual(vc[0], 2)
        self.assertRaises(ValueError, vc.set_timestamp, 0, 1)

    def test_comparisons(self):
        vc1 = VectorClock(2, 0, 0)
        vc2 = VectorClock(2, 2, 2)

        self.assertTrue(vc1 < vc2)
        self.assertTrue(vc1 <= vc2)

        vc3 = VectorClock(3, 0, 0)
        self.assertFalse(vc2 <= vc3)
        self.assertFalse(vc3 <= vc2)
        self.assertTrue(vc2 | vc3)

