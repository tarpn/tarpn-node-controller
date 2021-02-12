import logging
import threading
from abc import ABC
from concurrent.futures.thread import ThreadPoolExecutor
from typing import List, Callable, Any

from tarpn.log import LoggingMixin
from tarpn.util import Timer, ThreadingTimer


class CloseableThread(threading.Thread, ABC):
    def __init__(self, name: str, target=None):
        threading.Thread.__init__(self, name=name, target=target)

    def close(self):
        raise NotImplementedError()


class CloseableThreadLoop(CloseableThread, ABC):
    def __init__(self, name: str):
        super().__init__(name)
        self.closed = threading.Event()

    def run(self):
        while not self.closed.is_set() and self.is_alive():
            self.iter_loop()

    def close(self):
        self.closed.set()

    def iter_loop(self):
        raise NotImplementedError()


class Scheduler(LoggingMixin):
    def __init__(self):
        self.executor = ThreadPoolExecutor()
        self.threads: List[CloseableThread] = list()
        self.shutdown_tasks: List[Callable[..., Any]] = list()
        super().__init__(logging.getLogger("main"))

    def timer(self, delay: float, cb: Callable[[], None], auto_start=False) -> Timer:
        timer = ThreadingTimer(delay, cb)
        self.shutdown_tasks.append(timer.cancel)
        if auto_start:
            timer.start()
        return timer

    def submit(self, thread: CloseableThread):
        thread.start()
        self.threads.append(thread)

    def run(self, runnable: Callable[..., Any]):
        self.executor.submit(runnable)

    def add_shutdown_hook(self, runnable: Callable[..., Any]):
        self.shutdown_tasks.append(runnable)

    def join(self):
        for thread in self.threads:
            thread.join()
        self.executor.shutdown(wait=True, cancel_futures=False)

    def shutdown(self):
        self.info("Shutting down")

        # Try to stop the threads nicely
        for thread in self.threads:
            try:
                if thread.is_alive():
                    thread.close()
                    thread.join(5)
                    if not thread.is_alive():
                        self.info(f"Successfully closed thread {thread.name}")
                    else:
                        self.info(f"Did not close thread {thread.name} in time")
            except Exception:
                self.exception(f"Failed to close {thread.name} during shutdown")

        # Forcibly shutdown remaining tasks
        self.executor.shutdown(wait=False, cancel_futures=True)

        # Run shutdown hooks
        for task in self.shutdown_tasks:
            try:
                task()
            except Exception:
                self.exception(f"Failure to run shutdown task {task}")
        self.info("Finished shutdown")
