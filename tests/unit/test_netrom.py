import unittest

from tarpn.ax25 import AX25Call
from tarpn.netrom import NetRomInfo, OpType, parse_netrom_nodes, encode_netrom_nodes
from tarpn.netrom.router import NetRomRoutingTable
from tarpn.settings import NetworkConfig


class TestNetRom(unittest.TestCase):

    def test_settings(self):
        config = NetworkConfig.from_dict({})
        assert config.nodes_interval() > 0

    def test_nodes(self):
        nodes_payload = b'\xffDAVID \x9cf\x98\xa8\xac@\x04DOUG  \x96\x9ch\x9e\xa4\x84dz\xaeh\x8a\x92\xa0@dDAVE  \x96' \
                        b'\x9ch\x9e\xa4\x84d_\x96\x9ah\x8a\xa0@\x04JAY   \x96\x9ch\x9e\xa4\x84dz\x96\xaeh\x96\xb4@dA' \
                        b'NOIA \x96\x9ch\x9e\xa4\x84d_\x96\x82d\x88\x8a\xaefFFVC  \x96\x9ch\x9e\xa4\x84dz\x96h\x8c' \
                        b'\x88@@dDAN   \x96\x9ch\x9e\xa4\x84d_\x96\x9ah\x92\x8c\xaa\x04ERECH \x96\x9ch\x9e\xa4\x84d' \
                        b'\x9c\x96h\x92\xa0\x9e@\x04GREG  \x96\x9ch\x9e\xa4\x84d_'
        nodes = parse_netrom_nodes(nodes_payload)
        assert nodes.sending_alias == "DAVID"

        re_encoded = encode_netrom_nodes(nodes)
        assert re_encoded == nodes_payload

        table = NetRomRoutingTable("DAVID")
        table.update_routes(AX25Call("K4DBZ", 2), 0, nodes)
        print(table)

        routes = table.route(NetRomInfo(AX25Call("K4DBZ", 2), AX25Call("N3LTV", 2), 0, 0, 0, 0, 0, OpType.Information,
                                        "Hello, Doug".encode("ascii")))
        print(routes)
        print(table.get_nodes())

        #nodes.save(AX25Call("K4DBZ", 2), "nodes.json")

