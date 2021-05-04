SHELL=/bin/bash


.PHONY: init
init:
	python3 -m venv venv

.PHONY: deps
deps:
	. venv/bin/activate; pip install -e .[develop]
	rm -r venv/lib/python3.7/site-packages/tests

.PHONY: dist
dist:
	. venv/bin/activate; python setup.py sdist
	ls dist

.PHONY: test
test:
	. venv/bin/activate; py.test

.PHONY: clean
clean: ptys-down
	rm socat_*
	rm -r dist

ptys-up: pty-A pty-B pty-C
	ps | grep socat

ptys-down: 
	pkill socat
	ps | grep socat

pty-A:
	socat -x -d -d PTY,raw,echo=1,link=/tmp/vmodem_A0 PTY,raw,echo=0,link=/tmp/vmodem_A1 &> socat_A.log &

pty-B:
	socat -x -d -d PTY,raw,echo=1,link=/tmp/vmodem_B0 PTY,raw,echo=0,link=/tmp/vmodem_B1 &> socat_B.log &

pty-C:
	socat -x -d -d PTY,raw,echo=1,link=/tmp/vmodem_C0 PTY,raw,echo=0,link=/tmp/vmodem_C1 &> socat_C.log &


node-1:
	. venv/bin/activate; tarpn-node config/config-1.ini

node-2:
	. venv/bin/activate; tarpn-node config/config-2.ini

node-3:
	. venv/bin/activate; tarpn-node config/config-3.ini

nodes-down:
	pkill tarpn-node
