"""
Provides a timeslice logging decorator
"""

import logging
import threading
import time
import functools

logger = logging.getLogger(__name__)

def timelog(*, log: logging.Logger, level: int = logging.INFO):
    """Logs the execution time of the decorated function.
    Always place it beneath other decorators.
    """

    def decorator(func):
        mod_name = func.__module__
        qualname = func.__qualname__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            thread_id = threading.get_ident()
            log.log(level,"%d:%s.%s start time elapse measurement ...",
                    thread_id,mod_name,qualname)
            start = time.time()
            result = func(*args, **kwargs)
            end = time.time()
            elapsed = (end - start) * 1000
            log.log(level,"%d:%s.%s end time elapse measurement %.5f (ms)",
                    thread_id,mod_name,qualname, elapsed)
            return result

        return wrapper

    return decorator
