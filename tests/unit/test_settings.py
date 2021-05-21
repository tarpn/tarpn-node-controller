import os
import unittest

from tarpn.settings import Settings


class SettingsTest(unittest.TestCase):
    def test_load(self):
        s = Settings("tests/config", ["config.ini"])
        print(os.getcwd())
        assert s.node_config().node_call() == "TEST-1"
        assert s.node_config().get("id.message").endswith("TEST-1")
        s.port_configs()
