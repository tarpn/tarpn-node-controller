from tarpn.app import Application, Context


class SysopApplication(Application):
    def on_connect(self, context: Context):
        print("connect")

    def on_disconnect(self, context: Context):
        print("disconnect")

    def on_error(self, context: Context, error: str):
        print("error")

    def read(self, context: Context, data: bytes):
        print(data)
