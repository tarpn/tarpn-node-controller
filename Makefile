SHELL=/bin/bash


.PHONY: init
init:
	python3 -m venv venv

.PHONY: deps
deps:
	. venv/bin/activate; pip install -e .[develop]

.PHONY: dist
dist:
	. venv/bin/activate; python setup.py sdist; pip wheel --no-index --no-deps --wheel-dir dist dist/*.tar.gz 
	ls dist

.PHONY: test
test:
	. venv/bin/activate; py.test

.PHONY: clean
clean: ptys-down
	rm -f socat_*
	rm -rf dist
	find . | grep -E "(__pycache__|\.pyc|\.pyo$$)" | xargs rm -rf

ptys-up: pty-A pty-B pty-C
	ps | grep socat

ptys-down: 
	pkill socat || true
	ps | grep socat

pty-A:
	socat -x -d -d PTY,raw,echo=1,link=/tmp/vmodem_A0 PTY,raw,echo=0,link=/tmp/vmodem_A1 &> build/socat_A.log &

pty-B:
	socat -x -d -d PTY,raw,echo=1,link=/tmp/vmodem_B0 PTY,raw,echo=0,link=/tmp/vmodem_B1 &> build/socat_B.log &

pty-C:
	socat -x -d -d PTY,raw,echo=1,link=/tmp/vmodem_C0 PTY,raw,echo=0,link=/tmp/vmodem_C1 &> build/socat_C.log &


node-1:
	. venv/bin/activate; tarpn-node config/config-1.ini

node-2:
	. venv/bin/activate; tarpn-node config/config-2.ini

node-3:
	. venv/bin/activate; tarpn-node config/config-3.ini

nodes-down:
	pkill tarpn-node
