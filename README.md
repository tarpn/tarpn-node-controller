# TARPN Node Controller

[![experimental](http://badges.github.io/stability-badges/dist/experimental.svg)](http://github.com/badges/stability-badges)
[![build](https://github.com/tarpn/tarpn-node-controller/actions/workflows/build.yml/badge.svg)](https://github.com/tarpn/tarpn-node-controller/actions/workflows/build.yml)

Pure Python implementation of a TNC (Terminal Node Controller).

## RaspberryPi Setup

Install Python 3.7, pip, and virtualenv

```
sudo apt-get install python3.7 python3-pip
pip3 install virtualenv
```

## Mac OS Setup

TODO

## Installation

Create a Python environment with Python 3.7+

```sh
python3 -m virtualenv ~/tarpn-env
```

Install a [release](https://github.com/tarpn/tarpn-node-controller/releases)

```sh
~/tarpn-env/bin/pip install https://github.com/tarpn/tarpn-node-controller/releases/download/v0.1.0/tarpn_core-0.1.0-py3-none-any.whl
```

## Configuration

A minimal configuration:

```ini
[node]
log.dir = /tmp/tarpn-logs
log.config = config/logging.ini
node.call = N0CALL
node.alias = NODE2
node.locator = FM05

[port:1]
port.enabled = True
port.type = serial
port.framing = kiss
kiss.checksum = false
serial.device = /dev/ttyACM0
serial.speed = 57600

[network]
mesh.address = 0f.42
mesh.ttl = 7

[app:tty]
app.call = N0CALL
app.alias = TTY
app.address = mesh://ff.ff:100
app.sock = /tmp/tarpn-tty.sock
```

At a minimum, set `node.call` and `node.locator`. The `mesh.address` must be coordinated within your network to be unique.

## Running

Run the core packet engine

```sh
~/tarpn-env/bin/tarpn-node2 config.ini
```

Run the demo app in a separate shell

```sh
~/tarpn-env/bin/tarpn-app app.py TTYAppPlugin /tmp/tarpn-tty.sock
```

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
docker build . -t tarpn
docker run tarpn:latest
```
