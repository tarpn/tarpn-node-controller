from typing import Optional, Callable

from pyformance import global_registry
from pyformance.meters import Meter, Counter, Timer, Histogram, Gauge, CallbackGauge


class MetricsMixin:
    def get_key(self, name, *extra):
        pkg = self.__class__.__module__
        clazz = self.__class__.__qualname__
        name_parts = [name]
        name_parts.extend([str(e) for e in extra])
        joined_name = ".".join(name_parts)
        return f"{pkg}.{clazz}:{joined_name}"

    def meter(self, name: str, *args) -> Meter:
        return global_registry().meter(self.get_key(name, *args))

    def counter(self, name: str, *args) -> Counter:
        return global_registry().counter(self.get_key(name, *args))

    def timer(self, name: str, *args) -> Timer:
        return global_registry().timer(self.get_key(name, *args))

    def hist(self, name: str, *args) -> Histogram:
        return global_registry().histogram(self.get_key(name, *args))

    def gauge(self, fun: Callable[[], float], name: str, *args) -> Gauge:
        return global_registry().gauge(key=self.get_key(name, *args), gauge=CallbackGauge(fun))