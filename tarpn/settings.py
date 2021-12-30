import configparser
import re
import sys
import os
from typing import Optional, Mapping, Iterator, Any, List, Dict

_default_settings = {
    "node": {
        "id.message": "Terrestrial Amateur Radio Packet Network node ${node.alias} op is ${node.call}",
        "id.interval": 600,
        "admin.enabled": False,
        "admin.listen": "0.0.0.0",
        "admin.port": 8888
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
    "serial.timeout": 0.100
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
    def __init__(self, basedir: str = None, paths: List[str] = None, defaults: Dict = None):
        self._init_basedir(basedir)
        self._configfiles = [os.path.join(self._basedir, path) for path in paths]
        self._config: Optional[configparser.ConfigParser] = None
        if defaults is None:
            self._defaults = dict()
        else:
            self._defaults = defaults
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
        self._config = configparser.ConfigParser(defaults=self._defaults,
                                                 interpolation=configparser.ExtendedInterpolation(),
                                                 inline_comment_prefixes=";",
                                                 default_section="default")
        self._config.read_dict(_default_settings)
        for path in self._configfiles:
            if os.path.exists(path):
                self._config.read(path)
            else:
                raise RuntimeError(f"No such config file {path}")

    def save(self):
        # self._config.write()
        return

    def node_config(self):
        return NodeConfig(self._config["node"])

    def port_configs(self):
        ports = []
        for section in self._config.sections():
            m = re.match(r"port:(\d+)", section)
            if m:
                ports.append(int(m.group(1)))
        port_configs = []
        for port in ports:
            port_sect = self._config[f"port:{port}"]
            port_configs.append(PortConfig.from_dict(port, port_sect))
        return port_configs

    def network_configs(self):
        return NetworkConfig(self._config["network"])

    def app_configs(self):
        apps = []
        for section in self._config.sections():
            m = re.match(r"app:(\w[\w\d]*)", section)
            if m:
                apps.append(m.group(1))
        app_configs = []
        for app in apps:
            app_sect = self._config[f"app:{app}"]
            app_configs.append(AppConfig.from_dict(app, app_sect))
        return app_configs

    def config_section(self, name):
        return Config(name, self._config[name])


class Config(Mapping):
    def __init__(self, section_name, config_section):
        self._section = section_name
        self._config_section = config_section

    def __getitem__(self, k) -> Any:
        return self._config_section[k]

    def __len__(self) -> int:
        return len(self._config_section)

    def __iter__(self) -> Iterator:
        return iter(self._config_section)

    def __repr__(self) -> str:
        return f"{self._section}: {dict(self._config_section)}"

    def as_dict(self) -> dict:
        return dict(self._config_section)

    def get(self, key, default: str = None) -> str:
        value = self._config_section.get(key)
        if value is None:
            value = default
        if value is None:
            raise KeyError(f"Unknown key {key} in section {self._section}")
        return value

    def get_int(self, key, default: int = None) -> int:
        value = self._config_section.getint(key)
        if value is None:
            value = default
        if value is None:
            raise KeyError(f"Unknown key {key} in section {self._section}")
        return value

    def get_float(self, key, default: float = None) -> float:
        value = self._config_section.getfloat(key)
        if value is None:
            value = default
        if value is None:
            raise KeyError(f"Unknown key {key} in section {self._section}")
        return value

    def get_boolean(self, key, default: bool = None) -> bool:
        value = self._config_section.getboolean(key)
        if value is None:
            value = default
        if value is None:
            raise KeyError(f"Unknown key {key} in section {self._section}")
        return value


class NodeConfig(Config):
    def __init__(self, config_section):
        super().__init__("node", config_section)

    def node_call(self) -> str:
        return super().get("node.call")

    def node_alias(self) -> str:
        return super().get("node.alias")

    def admin_enabled(self) -> bool:
        return super().get_boolean("admin.enabled")

    def admin_port(self) -> int:
        return super().get_int("admin.port")

    def admin_listen(self) -> str:
        return super().get("admin.listen")


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


class AppConfig(Config):
    def __init__(self, app_name, app_config):
        super().__init__(f"app:{app_name}", app_config)
        self._app_name = app_name

    def app_name(self):
        return self._app_name

    def app_call(self):
        return super().get("app.call")

    def app_alias(self):
        return super().get("app.alias")

    def app_socket(self):
        return super().get("app.sock")

    def app_module(self):
        return super().get("app.module")

    @classmethod
    def from_dict(cls, app_name: str, configs: dict):
        parser = configparser.ConfigParser()
        parser.read_dict({f"app:{app_name}": configs})
        config = parser[f"app:{app_name}"]
        return cls(app_name, config)
