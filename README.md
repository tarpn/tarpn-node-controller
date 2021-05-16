# TARPN Node Controller

[![experimental](http://badges.github.io/stability-badges/dist/experimental.svg)](http://github.com/badges/stability-badges)

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

Run the core packet engine

```sh
~/tarpn-env/bin/tarpn-node2 config.ini
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
