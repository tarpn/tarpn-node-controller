# Serial ports

This application will make heavy use of serial ports since that's how our TNCs
are connected to the PC. Here's a simple way to test things out without a real
serial device

Create a pair of pseudo-terminals

```
socat -d -d PTY,raw,echo=1,link=/tmp/vmodem0 PTY,raw,echo=0,link=/tmp/vmodem1
```

Run the serial dump tool to monitor one end of the pair(speed doesn't matter)

```
python tools/serial_dump.py /tmp/vmodem0 9600
```

Send some data to the _other_ end of the pair

```
cat README.md /tmp/vmodem1
```
