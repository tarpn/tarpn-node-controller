#!/usr/bin/env python

class MyApp:
    def __init__(self, context, environ, *args, **kwargs):
        self.context = context
        print(environ)

    def on_connect(self, address):
        print(f"MyApp got connection from {address}")
        self.context.write(address, f"Welcome, {address}!".encode("ASCII"))

    def on_data(self, address, data):
        print(f"MyApp got data from {address}: {repr(data)}")
        self.context.write(address, f"You said: {repr(data)}".encode("ASCII"))

    def on_disconnect(self, address):
        print(f"MyApp got disconnected from {address}")
