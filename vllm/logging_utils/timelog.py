"""
Provides a timeslice logging decorator
"""

import os
import threading
import time
import functools

# pylint: disable=global-statement

_time_log_stack: dict[int,list[int]] = {}
_TIME_ORDER = 0
_time_log_lock = threading.Lock()

def timelog(func):
    """Logs the execution time of the decorated function.
    Always place it beneath other decorators.
    """
    mod_name = func.__module__
    qualname = func.__qualname__

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _TIME_ORDER
        pid = os.getpid()
        tid = threading.get_ident()

        order = 0
        l = 0
        with _time_log_lock:
            stack = _time_log_stack.get(tid,[])
            if len(stack) > 0:
                l = stack[-1]

            l += 1
            stack.append(l)
            _time_log_stack[tid] = stack
            _TIME_ORDER += 1
            order = _TIME_ORDER

        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start

        with _time_log_lock:
            stack = _time_log_stack.get(tid,None)
            if stack is not None:
                stack.pop()

        print(f"## timelog ## {pid} {tid} {order} {l} {mod_name}.{qualname} {elapsed}",flush=True)

        return result

    return wrapper
