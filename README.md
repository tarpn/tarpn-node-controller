# TARPN Node Controller

[![experimental](http://badges.github.io/stability-badges/dist/experimental.svg)](http://github.com/badges/stability-badges)

[![build](https://github.com/tarpn/tarpn-node-controller/actions/workflows/build.yml/badge.svg)](https://github.com/tarpn/tarpn-node-controller/actions/workflows/build.yml)

Pure Python implementation of a TNC (Terminal Node Controller).

# Installation

## System Requirements

On Linux (including Raspbian) install Python 3.7, pip, and virtualenv

```
sudo apt-get install python3.7 python3-pip
pip3 install virtualenv
```

On Mac OS:

```
brew install python3
pip3 install virtualenv
```

## Install the software

Create a Python environment with Python 3.7+ and enter it

```sh
python3 -m virtualenv ~/tarpn-env; cd ~/tarpn-env
```

_All further steps in this section assume you are in the ~/tarpn-env directory_

Install a [release](https://github.com/tarpn/tarpn-node-controller/releases)

```sh
./bin/pip install --upgrade https://github.com/tarpn/tarpn-node-controller/releases/download/v0.1.1/tarpn_core-0.1.1-py3-none-any.whl
```

## Configuration

Edit the included config file `config/node.ini`. At a minimum, set `mycall` and `node.locator`. 
The `mesh.address` must be coordinated within your network to be unique. Below is the included config file

```ini
[default]
mycall = N0CALL

[node]
log.dir = logs
log.config = config/logging.ini

node.call = ${mycall}-1
node.alias = NODE1
node.locator = FM06rb

[port:1]
port.enabled = False
port.type = serial
port.framing = kiss
kiss.checksum = false
serial.device = /tmp/vmodem_A0
serial.speed = 9600

[network]
mesh.address = 00.aa
mesh.ttl = 7

[app:demo]
app.address = mesh://ff.ff:100
app.call = ${mycall}-2
app.alias = DEMO
app.sock = /tmp/tarpn-demo.sock
```

# Usage

Run the core packet engine

```sh
./bin/tarpn-node
```

In a separate shell, run the demo app. 

```sh
./tarpn-app plugins.demo DemoApp /tmp/tarpn-demo.sock
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
docker build . -t tarpn
docker run tarpn:latest
```
