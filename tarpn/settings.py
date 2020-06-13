import configparser
import re
import sys
import os

_default_settings = {
    "node": {
        "id.message": "Terrestrial Amateur Radio Packet Network node ${node.alias} op is ${node.call}",
        "id.interval": 600
    }
}

_default_port_settings = {
    "port.enabled": True,
    "l2.ack.delay": 30,
    "l2.retry.count": 20,
    "l2.retry.timeout": 4000,
    "l2.idle.timeout": 180000
}


def _default_basedir(applicationName):
    # taken from http://stackoverflow.com/questions/1084697/how-do-i-store-desktop-application-data-in-a-cross-platform-way-for-python
    if sys.platform == "darwin":
        import appdirs
        return appdirs.user_data_dir(applicationName, "")
    elif sys.platform == "win32":
        return os.path.join(os.environ["APPDATA"], applicationName)
    else:
        return os.path.expanduser(os.path.join("~", "." + applicationName.lower()))


class Settings:
    def __init__(self, basedir=None):
        self._init_basedir(basedir)
        self._configfile = os.path.join(self._basedir, "config.ini")
        self._config: configparser.ConfigParser = None
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
        self._config.write()

    def node_config(self):
        return NodeConfig(self._config["node"])

    def port_configs(self):
        ports = []
        for section in self._config.sections():
            m = re.match(r"port:(\d)", section)
            if m:
                ports.append(int(m.group(1)))
        for port in ports:
            port_sect = self._config[f"port:{port}"]
            PortConfig.from_dict(port, port_sect)


class Config:
    def __init__(self, section_name, config_section):
        self._section = section_name
        self._config_section = config_section

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
        super().__init__("port", port_config)
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
