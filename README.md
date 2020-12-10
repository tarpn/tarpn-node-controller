# TARPN Node Controller

[![experimental](http://badges.github.io/stability-badges/dist/experimental.svg)](http://github.com/badges/stability-badges)

Pure Python implementation of a TNC (Terminal Node Controller).

# Development

## Local setup

Create a virtualenv, activate, and install deps (using Python 3)

```
make init
make deps
```

Run the tests

```
make test
```

Now some "tarpn-" scripts are in your path. E.g.,

```
tarpn-packet-dump /tmp/vmodem0 9600
```

Run flake8

```
flake8 tarpn
```

## RaspberryPi Setup

Install Python 3.7, pip, and virtualenv

```
sudo apt-get install python3.7 python3-pip
pip3 install virtualenv
```

Create a virtual python environment for tarpn-node-controller to run in and install it

```
python3 -m virtualenv venv
source venv/bin/activate
python setup.py install
```



## Docker setup

```
docker build . -t tarpn
docker run tarpn:latest
```

# References

* https://tinkering.xyz/async-serial/
