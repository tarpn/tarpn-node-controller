import asyncio
from collections import defaultdict
from dataclasses import dataclass
import logging
from typing import Dict, List, Callable

logger = logging.getLogger("events")


@dataclass
class EventListener:
    listener_key: str
    listener_name: str
    callback: Callable[..., None]


class EventBus:
    """
    Simple asynchronous event bus. Uses static members for singleton access.
    """
    listeners_by_key: Dict[str, List[EventListener]] = defaultdict(list)
    listeners_by_name: Dict[str, EventListener] = dict()

    @staticmethod
    def emit(key: str, *event):
        logger.debug(f"{key}: {event}")
        for listener in EventBus.listeners_by_key[key]:
            logger.debug(f"{key}: {event} {listener}")
            asyncio.get_event_loop().call_soon(listener.callback, *event)

    @staticmethod
    def bind(listener: EventListener, replace=False):
        if (listener.listener_name not in EventBus.listeners_by_name) or replace:
            logger.debug(f"Binding listener {listener.listener_name} to {listener.listener_key}")
            EventBus.listeners_by_key[listener.listener_key].append(listener)
            EventBus.listeners_by_name[listener.listener_name] = listener
        else:
            raise RuntimeError(f"A listener with name {listener.listener_name} is already bound")

    @staticmethod
    def remove(listener_name: str):
        logger.debug(f"Removing listener {listener_name}")
        listener = EventBus.listeners_by_name.get(listener_name)
        if listener:
            del EventBus.listeners_by_name[listener_name]
            EventBus.listeners_by_key[listener.listener_key].remove(listener)

