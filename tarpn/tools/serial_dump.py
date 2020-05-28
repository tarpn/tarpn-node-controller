import argparse

import hexdump
import serial


def main():
    """
    A utility to read from a serial port and print to stdout as hex
    """

    parser = argparse.ArgumentParser(description='Read data from a serial port and print to stdout')
    parser.add_argument("port", help="Serial port to open")
    parser.add_argument("baud", type=int, help="Baudrate to use")
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=1) as ser:
        while True:
            d = ser.read(100)
            if len(d) > 0:
                print(hexdump.hexdump(d))
