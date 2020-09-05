SHELL=/bin/bash


ptys-up: pty-A pty-B pty-C
        ps

ptys-down: 
        pkill socat

pty-A:
        socat -x -d -d PTY,raw,echo=1,link=/tmp/vmodem_A0 PTY,raw,echo=0,link=/tmp/vmodem_A1 &> socat_A.log &

pty-B:
        socat -x -d -d PTY,raw,echo=1,link=/tmp/vmodem_B0 PTY,raw,echo=0,link=/tmp/vmodem_B1 &> socat_B.log &

pty-C:
        socat -x -d -d PTY,raw,echo=1,link=/tmp/vmodem_C0 PTY,raw,echo=0,link=/tmp/vmodem_C1 &> socat_C.log &

clean: ptys-down
        rm socat_*

node-1:
        source venv/bin/activate
        tarpn-node config-1.ini

node-2:
        source venv/bin/activate
        tarpn-node config-2.ini

node-3:
        source venv/bin/activate
        tarpn-node config-3.ini

nodes-down:
        pkill tarpn-node
