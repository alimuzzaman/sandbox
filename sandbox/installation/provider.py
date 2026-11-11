"""Read-only root release metadata for actual service-process composition."""
from .binding import InstallationBindingError, _validate_authority
from .lifecycle import release_descriptor
from .paths import ManagedPaths
from .root_store import RootDirectory


def installed_release(binding, *, paths=ManagedPaths()):
    """Return fixed trusted executable paths, never a caller's home/source path.

    The root registration is nonsecret and may be group-readable by the selected
    service. It is metadata only: live handler/listener assembly is still needed
    before an execution capability's verifier may return true.
    """
    _validate_authority(binding)
    with RootDirectory(paths.config, private=False) as config:
        value = config.read('installation.json', private=False)
    fields = {'schema_version', 'installation_id', 'incarnation', 'machine_identity',
              'runtime_revision', 'policy_digest', 'bundle_digest', 'source_revision'}
    if (set(value) != fields or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or any(value.get(key) != binding.get(key) for key in ('installation_id', 'incarnation',
                'machine_identity', 'runtime_revision', 'policy_digest'))):
        raise InstallationBindingError('installation_release_unavailable', 'root release registration does not match current installation')
    release = release_descriptor(binding['runtime_revision'], paths)
    if any(value[key] != release[key] for key in ('bundle_digest', 'source_revision')):
        raise InstallationBindingError('installation_release_drift', 'root immutable release differs from its registration')
    root = paths.releases / binding['runtime_revision']
    _validate_authority(binding)
    return {**value, 'executable': str(root / 'sb'), 'python': str(root / '.cli-venv/bin/python'),
            'root': str(root)}
