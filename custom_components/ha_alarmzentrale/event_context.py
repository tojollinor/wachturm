"""Correlate records from one synchronous operation without time heuristics."""
from contextvars import ContextVar
from functools import wraps
from inspect import iscoroutinefunction
from uuid import uuid4

current_event = ContextVar("alarm_event", default=None)


def correlated(function):
    if iscoroutinefunction(function):
        @wraps(function)
        async def async_wrapped(*args, **kwargs):
            if current_event.get() is not None:
                return await function(*args, **kwargs)
            token = current_event.set(uuid4().hex)
            try:
                return await function(*args, **kwargs)
            finally:
                current_event.reset(token)
        return async_wrapped
    @wraps(function)
    def wrapped(*args, **kwargs):
        if current_event.get() is not None:
            return function(*args, **kwargs)
        token = current_event.set(uuid4().hex)
        try:
            return function(*args, **kwargs)
        finally:
            current_event.reset(token)
    return wrapped
