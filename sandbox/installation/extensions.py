"""Trusted composition registers actual installed capability verifiers here.

A verifier must prove handler, immutable release and any live supervisor handoff;
configuration or release descriptors alone never authorize capability claims.
"""
from collections.abc import Callable, Mapping
import re

_PROVIDERS: dict[str, tuple[int, Callable]] = {}


def register_capability(name: str, version: int, verifier: Callable) -> None:
    if (not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,79}', name)
            or type(version) is not int or version < 1 or not callable(verifier)):
        raise ValueError('installation capability registration is invalid')
    if name in _PROVIDERS:
        raise ValueError('installation capability already registered')
    _PROVIDERS[name] = (version, verifier)


def installed_capabilities(binding: Mapping) -> dict[str, int]:
    result = {}
    for name, (version, verifier) in tuple(_PROVIDERS.items()):
        try:
            available = verifier(dict(binding)) is True
        except Exception:
            available = False
        if available:
            result[name] = version
    return result


# Descriptor metadata alone never registers an execution capability.
EXTENSION_MANIFEST = {
    'hosting.baseline.worker.v1': frozenset({'schema_version', 'installation_id', 'incarnation',
        'runtime_revision', 'policy_digest', 'image_id', 'source_digest', 'platform', 'protocol'}),
    'hosting.baseline.listeners.v1': frozenset({'schema_version', 'installation_id', 'incarnation',
        'runtime_revision', 'policy_digest', 'worker_release_digest', 'front_door', 'ingress_uids'}),
}


def read_extension(name: str, binding: Mapping) -> dict:
    """Read fixed root registration metadata; live listener FDs are never persisted."""
    import hashlib
    import json
    from .binding import InstallationBindingError, _directory, _read, _validate_authority
    from .paths import ManagedPaths
    if not isinstance(name, str) or name not in EXTENSION_MANIFEST:
        raise InstallationBindingError('installation_extension_unknown', 'extension is not registered by the source manifest')
    _validate_authority(binding)
    root = ManagedPaths().config
    _directory(root, 0)
    _directory(root / 'extensions', 0)
    record = _read(root / 'extensions' / (name + '.json'), 0, private=False)
    descriptor = record.get('descriptor')
    valid = (set(record) == {'schema_version', 'digest', 'descriptor'}
        and type(record.get('schema_version')) is int and record['schema_version'] == 1
        and isinstance(descriptor, dict) and set(descriptor) == EXTENSION_MANIFEST[name]
        and type(descriptor.get('schema_version')) is int and descriptor['schema_version'] == 1)
    if valid:
        valid = all(descriptor.get(key) == binding.get(key) for key in
                    ('installation_id', 'incarnation', 'runtime_revision', 'policy_digest'))
    if valid and name == 'hosting.baseline.worker.v1':
        valid = (isinstance(descriptor.get('image_id'), str)
            and re.fullmatch(r'sha256:[0-9a-f]{64}', descriptor['image_id']) is not None
            and isinstance(descriptor.get('source_digest'), str)
            and re.fullmatch(r'[0-9a-f]{64}', descriptor['source_digest']) is not None
            and descriptor['platform'] == 'linux/amd64'
            and type(descriptor['protocol']) is int and descriptor['protocol'] == 1)
    if valid and name == 'hosting.baseline.listeners.v1':
        uids = descriptor.get('ingress_uids')
        valid = (descriptor.get('front_door') in {'nginx', 'caddy'}
            and isinstance(descriptor.get('worker_release_digest'), str)
            and re.fullmatch(r'[0-9a-f]{64}', descriptor['worker_release_digest']) is not None
            and isinstance(uids, list) and 1 <= len(uids) <= 4
            and all(type(uid) is int and uid >= 0 for uid in uids) and len(set(uids)) == len(uids))
    if not valid:
        raise InstallationBindingError('installation_extension_invalid', 'extension descriptor or binding is invalid')
    digest = hashlib.sha256(json.dumps(descriptor, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if record['digest'] != digest:
        raise InstallationBindingError('installation_extension_invalid', 'extension descriptor digest does not match')
    _validate_authority(binding)
    return {'digest': digest, 'descriptor': descriptor}
