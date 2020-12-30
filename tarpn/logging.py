from logging import Logger


class LoggingMixin:
    def __init__(self, logger, extra_func):
        self.logger: Logger = logger
        self.extra_func = extra_func

    def info(self, msg, *args, **kwargs):
        self.logger.info(f"{self.extra_func()} {msg}", *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        self.logger.debug(f"{self.extra_func()} {msg}", *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self.logger.warning(f"{self.extra_func()} {msg}", *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        self.logger.exception(f"{self.extra_func()} {msg}", *args, **kwargs)
