# TARPN Node Controller

[![experimental](http://badges.github.io/stability-badges/dist/experimental.svg)](http://github.com/badges/stability-badges)

[![build](https://github.com/tarpn/tarpn-node-controller/actions/workflows/build.yml/badge.svg)](https://github.com/tarpn/tarpn-node-controller/actions/workflows/build.yml)

Pure Python implementation of a TNC (Terminal Node Controller).

# Installation

## System Requirements

On Linux (including Raspbian) install Python 3.7, pip, and virtualenv

```shell
sudo apt-get install python3.7 python3-pip
pip3 install virtualenv
```

On Mac OS:

```shell
brew install python3
pip3 install virtualenv
```

## Install the software (RPi)

Create an installation directory under `/opt` and set it up as a [Python virtual environment](https://github.com/tarpn/tarpn-node-controller#python-virtual-environment).

_These instructions assume an installation directory of /opt/tarpn. However, the software can be installed at any user-writeable location_

```shell
sudo mkdir /opt/tarpn-core
sudo chown pi:pi /opt/tarpn-core
python3 -m virtualenv /opt/tarpn-core
cd /opt/tarpn-core
```

Install a [release](https://github.com/tarpn/tarpn-node-controller/releases)

```shell
/opt/tarpn-core/bin/pip install --upgrade https://github.com/tarpn/tarpn-node-controller/releases/download/v0.1.4/tarpn-core-0.1.4.tar.gz
```

## Configuration

A sample config file is included at `/opt/tarpn-core/config/node.ini.sample`. Make a copy of it at `/opt/tarpn-core/config/node.ini`

```shell
cp /opt/tarpn-core/config/node.ini.sample /opt/tarpn-core/config/node.ini
```

Edit this file and, at a minimum, set `mycall`, `node.alias`, and `node.locator`.

One or more ports should also be configured. An example `[port:1]` is included.

The `mesh.address` must be coordinated within your network to be unique.

node.ini.sample:
```ini
[default]
mycall = N0CALL         ; Your callsign

[node]
node.alias = MyNode     ; A name for your node
node.locator = FM05     ; Your maidenhead locator

[port:1]
port.enabled = False
port.type = serial
port.framing = kiss
kiss.checksum = false
serial.device = /dev/ttyUSB0
serial.speed = 57600

[network]
mesh.address = 00.aa
```

## Systemd service (optional)

Install the systemd service file. This assumes that you've configured tarpn-node

```shell
wget https://raw.githubusercontent.com/tarpn/tarpn-node-controller/scripts/tarpn-core.service
sudo mv tarpn-core.service /etc/systemd/system/tarpn-core.service
sudo systemctl reload-daemon
sudo systemctl enable tarpn-core
sudo systemctl start tarpn-core
```

## Python Virtual Environment

These instructions will install the software into what is known as a Python virtual environment (sometimes called a "venv"). This is a directory that contains an isolated set of libraries for Python to use. This way, a project can use different versions of libraries than the ones that are installed in the system-wide Python environment (as you would get from doing apt-get install python-serial, for example).

Additional information:
* https://docs.python.org/3/tutorial/venv.html
* https://realpython.com/python-virtual-environments-a-primer/

# Usage

Run the core packet engine

```sh
/opt/tarpn-core/bin/tarpn-node
```

In a separate shell, run the demo app. 

```sh
/opt/tarpn-core/bin/tarpn-app demo
```

This app is a simple terminal chat program that lets you easily test out
the network with other participants.

# Development

## Local setup

Create a virtualenv, activate, and install deps (using Python 3)

```sh
make init
make deps
```

Run the tests

```sh
make test
```

Now some "tarpn-" scripts are in your path. E.g.,

```sh
tarpn-packet-dump /tmp/vmodem0 9600
```

Run flake8

```sh
flake8 tarpn
```


## Docker setup

```sh
docker build . -t tarpn/tarpn-core
docker run tarpn/tarpn-core:latest
```
