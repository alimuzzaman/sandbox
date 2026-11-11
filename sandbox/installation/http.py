"""Dependency-light discovery handler, mounted behind existing ControlGate."""
import asyncio
from .binding import InstallationBindingError, read_installation_binding


def envelope(*, binding=None, error=None):
    return {'ok': error is None, 'installation_schema': 1, 'binding': binding, 'error': error}


async def installation_response(request, *, home):
    if request.query_params:
        return 400, envelope(error={'code': 'installation_request_invalid', 'message': 'discovery accepts no input'})
    async for chunk in request.stream():
        if chunk:
            return 400, envelope(error={'code': 'installation_request_invalid', 'message': 'discovery accepts no input'})
    try:
        binding = await asyncio.to_thread(read_installation_binding, home=home)
    except InstallationBindingError as exc:
        return 409, envelope(error={'code': exc.code, 'message': str(exc)})
    return 200, envelope(binding=binding)
