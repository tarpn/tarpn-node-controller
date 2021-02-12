from typing import Optional

from pyformance import global_registry
from pyformance.meters import Meter, Counter, Timer


class MetricsMixin:
    def _name(self, name):
        pkg = self.__class__.__module__
        clazz = self.__class__.__qualname__
        return f"{pkg}:{clazz}:{name}"

    def meter(self, name: str, event: Optional[str] = None) -> Meter:
        if event is not None:
            return global_registry().meter(self._name(f"{name}.{event}"))
        else:
            return global_registry().meter(self._name(name))

    def counter(self, name: str, event: Optional[str] = None) -> Counter:
        if event is not None:
            return global_registry().counter(self._name(f"{name}.{event}"))
        else:
            return global_registry().counter(self._name(name))

    def timer(self, name: str, event: Optional[str] = None) -> Timer:
        if event is not None:
            return global_registry().timer(self._name(f"{name}.{event}"))
        else:
            return global_registry().timer(self._name(name))
