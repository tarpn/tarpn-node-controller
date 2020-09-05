import configparser
import re
import sys
import os
from typing import Optional

_default_settings = {
    "node": {
        "id.message": "Terrestrial Amateur Radio Packet Network node ${node.alias} op is ${node.call}",
        "id.interval": 600
    },
    "network": {
        "netrom.ttl": 7,
        "netrom.obs.min": 4,
        "netrom.obs.init": 6,
        "netrom.nodes.quality.min": 73,
        "netrom.nodes.interval": 300
    }
}

_default_port_settings = {
    "port.enabled": True,
    "l2.ack.delay": 30,
    "l2.retry.count": 20,
    "l2.retry.timeout": 4000,
    "l2.idle.timeout": 180000
}


def _default_basedir(app_name):
    # taken from http://stackoverflow.com/questions/1084697/
    if sys.platform == "darwin":
        import appdirs
        return appdirs.user_data_dir(app_name, "")
    elif sys.platform == "win32":
        return os.path.join(os.environ["APPDATA"], app_name)
    else:
        return os.path.expanduser(os.path.join("~", "." + app_name.lower()))


class Settings:
    def __init__(self, basedir=None, path="config.ini"):
        self._init_basedir(basedir)
        self._configfile = os.path.join(self._basedir, path)
        self._config: Optional[configparser.ConfigParser] = None
        self.load()

    def _init_basedir(self, basedir):
        if basedir is not None:
            self._basedir = basedir
        else:
            self._basedir = _default_basedir("TARPN")

        if not os.path.isdir(self._basedir):
            try:
                os.makedirs(self._basedir)
            except Exception:
                print(f"Could not create base folder at {self._basedir}. This is a fatal error, TARPN "
                      "cannot run without a writable base folder.")
                raise

    def load(self):
        self._config = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
        self._config.read_dict(_default_settings)
        self._config.read(self._configfile)

    def save(self):
        # self._config.write()
        return

    def node_config(self):
        return NodeConfig(self._config["node"])

    def port_configs(self):
        ports = []
        for section in self._config.sections():
            m = re.match(r"port:(\d)", section)
            if m:
                ports.append(int(m.group(1)))
        port_configs = []
        for port in ports:
            port_sect = self._config[f"port:{port}"]
            port_configs.append(PortConfig.from_dict(port, port_sect))
        return port_configs

    def network_configs(self):
        return NetworkConfig(self._config["network"])


class Config:
    def __init__(self, section_name, config_section):
        self._section = section_name
        self._config_section = config_section

    def __repr__(self) -> str:
        return f"{self._section}: {dict(self._config_section)}"

    def get(self, key, default: str = None):
        value = self._config_section.get(key)
        if value is None:
            value = default
        if value is None:
            raise KeyError(f"Unknown key {key} in section {self._section}")
        return value

    def get_int(self, key, default: int = None):
        value = self._config_section.getint(key)
        if value is None:
            value = default
        if value is None:
            raise KeyError(f"Unknown key {key} in section {self._section}")
        return value

    def get_boolean(self, key, default: bool = None):
        value = self._config_section.getboolean(key)
        if value is None:
            value = default
        if value is None:
            raise KeyError(f"Unknown key {key} in section {self._section}")
        return value


class NodeConfig(Config):
    def __init__(self, config_section):
        super().__init__("node", config_section)

    def node_call(self):
        return super().get("node.call")

    def node_name(self):
        return super().get("node.name")


class PortConfig(Config):
    def __init__(self, port_id, port_config):
        super().__init__(f"port:{port_id}", port_config)
        self._port_id = port_id

    def port_id(self):
        return self._port_id

    def port_type(self):
        return super().get("port.type")

    @classmethod
    def from_dict(cls, port_id: int, configs: dict):
        parser = configparser.ConfigParser(defaults=_default_port_settings)
        parser.read_dict({f"port:{port_id}": configs})
        config = parser[f"port:{port_id}"]
        return cls(port_id, config)


class NetworkConfig(Config):
    def __init__(self, config_section):
        super().__init__("network", config_section)

    def ttl(self) -> int:
        return super().get_int("netrom.ttl")

    def min_obs(self) -> int:
        return super().get_int("netrom.obs.min")

    def init_obs(self) -> int:
        return super().get_int("netrom.obs.init")

    def min_qual(self) -> int:
        return super().get_int("netrom.nodes.quality.min")

    def nodes_interval(self) -> int:
        return super().get_int("netrom.nodes.interval")

    def node_call(self) -> str:
        return super().get("netrom.node.call")

    def node_alias(self) -> str:
        return super().get("netrom.node.alias")

    @classmethod
    def from_dict(cls, configs: dict):
        parser = configparser.ConfigParser(defaults=_default_settings["network"])
        parser.read_dict({f"network": configs})
        config = parser[f"network"]
        return cls(config)
