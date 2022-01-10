import json
import shlex
import subprocess
import unittest

import pytest


def curl(sock: str, url: str):
    command = f"curl --retry 5 --max-time 10 --retry-delay 0 --retry-max-time 40 -v --unix-socket {sock} {url}"
    result = subprocess.run(shlex.split(command), stdout=subprocess.PIPE)
    return result.stdout


class DockerTest(unittest.TestCase):
    def test_alice_alive(self):
        result = curl("/tmp/socks/tarpn-shell-alice.sock", "http://dummy/healthcheck")
        assert result == b"ok"

    def test_bob_alive(self):
        result = curl("/tmp/socks/tarpn-shell-bob.sock", "http://dummy/healthcheck")
        assert result == b"ok"

    def test_carol_alive(self):
        result = curl("/tmp/socks/tarpn-shell-carol.sock", "http://dummy/healthcheck")
        assert result == b"ok"

    @pytest.mark.timeout(120)
    def test_convergence(self):
        done = False
        while not done:
            alice_net = curl("/tmp/socks/tarpn-shell-alice.sock", "http://dummy/network")
            bob_net = curl("/tmp/socks/tarpn-shell-bob.sock", "http://dummy/network")
            carol_net = curl("/tmp/socks/tarpn-shell-carol.sock", "http://dummy/network")

            alice_node_data = json.loads(alice_net).get("nodes", [])
            bob_node_data = json.loads(bob_net).get("nodes", [])
            carol_node_data = json.loads(carol_net).get("nodes", [])

            if len(alice_node_data) == 2 and len(bob_node_data) == 2 and len(carol_node_data) == 2:
                break
