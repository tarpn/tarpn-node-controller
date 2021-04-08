#!/usr/bin/env python
import logging
import sys
import time

from tarpn.app.runner import NetworkApp

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


class MyApp(NetworkApp):
    def on_connect(self, address):
        print(f"MyApp got connection from {address}")
        self.context.write(address, f"Welcome, {address}!".encode("ASCII"))

    def on_data(self, address, data):
        print(f"MyApp got data from {address}: {repr(data)}")
        self.context.write(address, f"You said: {repr(data)}".encode("ASCII"))

    def on_disconnect(self, address):
        print(f"MyApp got disconnected from {address}")


class BasicChat:
    def __init__(self, context, environ, *args, **kwargs):
        self.context = context
        self.connected = set()
        logging.debug(f"Chat starting, environment is {environ}")

    def on_connect(self, address):
        logging.debug(f"New connection from {address}")
        self.context.write(address, f"Welcome, {address}!".encode("utf-8"))
        time.sleep(0.200)

        for remote_address in self.connected:
            if address == remote_address:
                continue
            logging.debug(f"Sending join notification for {address} to {remote_address}")
            self.context.write(remote_address, f"{address:<8} joined the chat".encode("utf-8"))
            time.sleep(0.200)

        self.connected.add(address)

    def on_data(self, address, data):
        self.connected.add(address)

        msg = str(data, "utf-8")
        logging.info(f"{address:<8}: {msg}")
        for remote_address in self.connected:
            if address == remote_address:
                continue
            logging.debug(f"Forwarding message from {address} to {remote_address}")
            self.context.write(remote_address, f"{address:<8}: {msg}".encode("utf-8"))
            time.sleep(0.200)

    def on_disconnect(self, address):
        logging.debug(f"Disconnected from {address}")
        self.connected.remove(address)

        for remote_address in self.connected:
            if address == remote_address:
                continue
            self.context.write(remote_address, f"{remote_address:<8} left the chat".encode("utf-8"))
            time.sleep(0.200)
