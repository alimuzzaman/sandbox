"""Managed maintenance admission is root policy, independent from peer authority."""
from .binding import InstallationBindingError, _directory, _read, is_managed_layout
from .paths import ManagedPaths

OBSERVATION_PATHS = frozenset({'/hosting/installation', '/diagnostics', '/inventory'})


def allowed_request(method: str, path: str, *, home=None, managed=False) -> bool:
    try:
        if not managed and not is_managed_layout(home=home):
            return True
        if method == 'GET' and path in OBSERVATION_PATHS:
            return True
        root = ManagedPaths().config
        _directory(root, 0)
        state = _read(root / 'admission.json', 0, private=False)
        return (set(state) == {'schema_version', 'open'}
                and type(state.get('schema_version')) is int and state['schema_version'] == 1
                and state.get('open') is True)
    except InstallationBindingError:
        return False
