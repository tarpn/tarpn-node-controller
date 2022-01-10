import math
import statistics
import time
import urllib
from typing import Optional, Dict

from flask import Flask, Response, request
from flask.testing import EnvironBuilder
from pyformance import global_registry

from tarpn.datalink import L2Queuing
from tarpn.network.mesh import MeshAddress
from tarpn.network.mesh.protocol import MeshProtocol
from tarpn.scheduler import Scheduler
from tarpn.settings import Settings
from tarpn.transport import Protocol, Transport
from tarpn.transport.mesh_l4 import MeshTransportManager


class TarpnCoreProtocol(Protocol):
    def __init__(self,
                 settings: Settings, port_queues: Dict[int, L2Queuing],
                 network: MeshProtocol, transport_manager: MeshTransportManager, scheduler: Scheduler):
        self.settings = settings
        self.port_queues = port_queues
        self.network = network
        self.transport_manager = transport_manager
        self.scheduler = scheduler

        self.transport: Optional[Transport] = None
        self.app: Flask = Flask("tarpn-core")
        self.register_handlers()

    def register_handlers(self):
        @self.app.route("/")
        def index():
            return "welcome"

        @self.app.route("/healthcheck")
        def healhcheck():
            return "ok"

        @self.app.route("/ports")
        def ports():
            port_data = []
            ports_settings = self.settings.port_configs()
            for port, queue in self.port_queues.items():
                port_config = ports_settings[port]
                port_data.append({
                    "id": port,
                    "name": port_config.port_name(),
                    "mtu": queue.mtu(),
                    "qsize": queue.qsize(),
                    "config": port_config.as_dict()
                })
            return {"ports": port_data}

        @self.app.route("/network")
        def network():
            out = {
                "address": str(self.network.our_address),
                "epoch": self.network.our_link_state_epoch
            }
            neighbors = []
            for addr, neighbor in self.network.neighbors.items():
                neighbors.append({
                    "address": str(addr),
                    "name": neighbor.name,
                    "last_seen": neighbor.last_seen,
                    "last_update": neighbor.last_update,
                    "status": str(neighbor.state)
                })
            out["neighbors"] = neighbors

            nodes = []
            for node, link_states in self.network.valid_link_states().items():
                epoch = self.network.link_state_epochs.get(node)
                nodes.append({
                    "address": str(node),
                    "epoch": epoch,
                    "link_states": str(link_states)  # TODO fix this
                })
            out["nodes"] = nodes
            return out

        @self.app.route("/routes")
        def routes():
            out = []

            for node, link_states in self.network.valid_link_states().items():
                epoch = self.network.link_state_epochs.get(node)
                path, path_cost = self.network.route_to(node)
                if len(path) == 0:
                    continue
                link = self.network.neighbors.get(path[1]).link_id
                device_id = self.network.link_multiplexer.get_link_device_id(link)
                out.append({
                    "address": str(node),
                    "epoch": epoch,
                    "path": [str(n) for n in path[1:]],
                    "port": device_id,
                    "cost": path_cost
                })
            return {"routes": out}

        @self.app.route("/ping", methods=["POST"])
        def ping():
            address = request.args.get("address")
            count = int(request.args.get("count", 3))
            run_async = request.args.get("async", None)
            timeout = int(request.args.get("timeout", 1000))
            size = int(request.args.get("size", 100))

            node = MeshAddress.parse(address)
            seqs = []
            times = []
            lost = 0

            def do_ping():
                nonlocal lost
                t0 = time.time_ns()
                seq = self.network.ping_protocol.send_ping(node, size=size)
                found = self.network.ping_protocol.wait_for_ping(node, seq, timeout_ms=timeout)
                t1 = time.time_ns()
                if found:
                    dt = int((t1 - t0) / 1000000.)
                    seqs.append(seq)
                    times.append(dt)
                else:
                    lost += 1

            if run_async:
                futures = []
                for _ in range(count):
                    futures.append(self.scheduler.run(do_ping))
                for fut in futures:
                    fut.result(3000)
            else:
                for _ in range(count):
                    do_ping()

            out = {
                "address": address,
                "seqs": seqs,
                "times": times,
                "lost": lost
            }
            if count > 1:
                out["min"] = min(times)
                out["max"] = max(times)
                out["avg"] = statistics.mean(times)
                out["stdev"] = statistics.stdev(times)
            return out

        @self.app.route("/metrics")
        def metrics():
            return global_registry().dump_metrics()

    def connection_made(self, transport: Transport):
        self.transport = transport

    def connection_lost(self, exc):
        self.transport = None

    def data_received(self, data: bytes):
        raw_lines = data.splitlines()
        first = raw_lines[0].decode("ascii")
        parts = first.split(" ")
        verb, path, http_version = parts[0:3]
        url_parts = urllib.parse.urlparse(path)
        # TODO parse headers?
        builder = EnvironBuilder(app=self.app,
                                 path=url_parts.path,
                                 query_string=url_parts.query,
                                 method=verb,
                                 data=b"\r\n".join(raw_lines[2:]),
                                 content_type="text/plain")
        env = builder.get_environ()

        with self.app.request_context(env):
            resp: Response = self.app.full_dispatch_request()
            buf = bytearray()
            buf.extend(f"HTTP/1.1 {resp.status}".encode("ISO-8859-1"))
            buf.extend(b"\r\n")
            buf.extend(str(resp.headers).encode("ISO-8859-1"))
            if resp.data:
                buf.extend(resp.data)
            self.transport.write(buf)  # TODO transport was None?
