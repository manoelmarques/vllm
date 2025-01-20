"""
Provides a timeslice logging decorator
"""

import logging
import os
import tempfile
import threading
import time
import functools
import pathlib

# pylint: disable=global-statement,invalid-name

_time_log_stack: dict[int,list[int]] = {}
_time_order = 0
_time_log_lock = threading.Lock()

def timelog(*, log: logging.Logger, level: int = logging.INFO):
    """Logs the execution time of the decorated function.
    Always place it beneath other decorators.
    """

    def decorator(func):
        mod_name = func.__module__
        qualname = func.__qualname__
        temp_path = pathlib.Path(tempfile.gettempdir())

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            global _time_order
            pid = os.getpid()
            tid = threading.get_native_id()

            order = 0
            l = 0
            with _time_log_lock:
                stack = _time_log_stack.get(tid,[])
                if len(stack) > 0:
                    l = stack[-1]

                l += 1
                stack.append(l)
                _time_log_stack[tid] = stack
                _time_order += 1
                order = _time_order

            start = time.time()
            result = func(*args, **kwargs)
            end = time.time()
            elapsed = end - start

            with _time_log_lock:
                stack = _time_log_stack.get(tid,None)
                if stack is not None:
                    stack.pop()

            log.log(level,"%d:%d:%s:%s.%s end time elapse measurement %f (s)",
                    pid,tid," " * l,mod_name,qualname, elapsed)
            file_path = temp_path.joinpath(f"vllm-{pid}.log")
            with open(file_path,"a", encoding="utf8") as file:
                file.write(f"{tid} {order} {l} {mod_name}.{qualname} {elapsed}\n")
            return result

        return wrapper

    return decorator
