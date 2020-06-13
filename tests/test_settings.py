import unittest

from tarpn.settings import Settings


class SettingsTest(unittest.TestCase):
    def test_load(self):
        s = Settings("config")
        assert s.node_config().node_call() == "TEST-1"
        print(s.node_config().get("id.message"))
        s.port_configs()
