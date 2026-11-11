"""Lease each registered tool's real execution, including streaming MCP sessions."""
from functools import wraps
import inspect
from typing import get_type_hints
from .maintenance import operation_guard


class OperationLeasedRegistrar:
    def __init__(self, server):
        self.server = server

    def __getattr__(self, name):
        return getattr(self.server, name)

    def tool(self, *args, **kwargs):
        register = self.server.tool(*args, **kwargs)
        def decorate(function):
            if inspect.isasyncgenfunction(function):
                @wraps(function)
                async def guarded(*a, **kw):
                    with operation_guard():
                        async for value in function(*a, **kw):
                            yield value
            elif inspect.iscoroutinefunction(function):
                @wraps(function)
                async def guarded(*a, **kw):
                    with operation_guard():
                        return await function(*a, **kw)
            elif inspect.isgeneratorfunction(function):
                @wraps(function)
                def guarded(*a, **kw):
                    with operation_guard():
                        yield from function(*a, **kw)
            else:
                @wraps(function)
                def guarded(*a, **kw):
                    with operation_guard():
                        return function(*a, **kw)
            # Resolve forward annotations in the original function's module,
            # not this wrapper's globals, before SDK schema construction.
            hints = get_type_hints(function)
            signature = inspect.signature(function)
            guarded.__annotations__ = hints
            guarded.__signature__ = signature.replace(
                parameters=[parameter.replace(annotation=hints.get(name, parameter.annotation))
                            for name, parameter in signature.parameters.items()],
                return_annotation=hints.get('return', signature.return_annotation))
            return register(guarded)
        return decorate
