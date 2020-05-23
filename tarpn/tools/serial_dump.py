import argparse
import sys

import hexdump
import serial

if __name__ == "__main__":
    """
    A utility to read from a serial port and print to stdout as hex
    """

    parser = argparse.ArgumentParser(description='Read data from a serial port and print to stdout')
    parser.add_argument("port", help="Serial port to open")
    parser.add_argument("baud", type=int, help="Baudrate to use")
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=1) as ser:
        d = ser.read(32)
        print(hexdump.dump(d))
