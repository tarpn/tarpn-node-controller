import logging
from functools import partial


class LoggingMixin:
    def __init__(self, logger: logging.Logger = None, extra_func=None):
        if logger is None:
            self.logger = logging.getLogger("main")
        else:
            self.logger = logger
        if extra_func is None:
            self.extra_func = partial(str, self.__class__.__qualname__)
        else:
            self.extra_func = extra_func

    def info(self, msg, *args, **kwargs):
        self.logger.info(f"{self.extra_func()} {msg}", *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        self.logger.debug(f"{self.extra_func()} {msg}", *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self.logger.warning(f"{self.extra_func()} {msg}", *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self.logger.error(f"{self.extra_func()} {msg}", *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        self.logger.exception(f"{self.extra_func()} {msg}", *args, **kwargs)
