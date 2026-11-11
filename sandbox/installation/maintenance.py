"""Root-owned maintenance admission and installation-wide operation leases."""
from contextlib import contextmanager, ExitStack
import os
import re

from .binding import InstallationBindingError, read_installation_identity
from .paths import ManagedPaths, selected_home
from .root_store import RootDirectory


def fail(code, message):
    raise InstallationBindingError(code, message)


def _state(store, identity):
    return _validate_state(store.read(identity + '.json', private=False), identity)


def _validate_state(value, identity):
    if (set(value) != {'schema_version', 'installation_id', 'request_id', 'closed'}
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value['installation_id'] != identity or type(value.get('closed')) is not bool
            or not isinstance(value.get('request_id'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value['request_id'])):
        fail('installation_maintenance_invalid', 'maintenance admission state is invalid')
    return value


@contextmanager
def operation_guard(*, home=None, paths=ManagedPaths()):
    """Shared lease for CLI operations, admitted requests and detached workers.

    Unprovisioned personal mode retains existing behavior. Managed mode never
    runs effects without its root-created anchor. Pure observers and lifecycle
    commands use separate observation/root-authority paths.
    """
    managed = os.environ.get('SANDBOX_MANAGED') == '1' or selected_home(home).resolve() == paths.state.resolve()
    try:
        identity = read_installation_identity(home=home)['installation_id']
    except InstallationBindingError as exc:
        if exc.code == 'installation_binding_uninitialized' and not managed:
            yield
            return
        raise
    if not os.path.lexists(paths.lease_root / (identity + '.lock')) and not managed:
        yield
        return
    with ExitStack() as stack:
        try:
            store = stack.enter_context(RootDirectory(paths.lease_root, private=False))
            stack.enter_context(store.lock(identity + '.lock', exclusive=False))
            if _state(store, identity)['closed']:
                fail('installation_admission_closed', 'installation admission is closed for maintenance')
        except BlockingIOError:
            fail('installation_maintenance_busy', 'installation maintenance owns execution authority')
        except OSError:
            fail('installation_maintenance_unavailable', 'managed operation lease is unavailable')
        yield


class RootMaintenance:
    """One administrator per installation; admission stays closed after failure."""
    def __init__(self, installation_id=None, *, paths=ManagedPaths(), fence=None):
        if installation_id is not None and (not isinstance(installation_id, str)
                or not re.fullmatch(r'[0-9a-f]{32}', installation_id)):
            fail('installation_binding_invalid', 'maintenance installation identity is invalid')
        self.identity = installation_id or 'fresh-managed'
        self.paths, self.fence = paths, fence
        self.request, self.stack, self.leases, self.admin = None, None, None, None

    def _publish(self, identity, value):
        # Durable root state survives reboot; /run is only its current lease
        # projection. A crash between writes leaves admission closed on restart.
        self.admin.write('maintenance.' + identity + '.json', value)
        self.leases.write(identity + '.json', value, mode=0o644)

    def _close(self, identity, request):
        try:
            with self.leases.lock(identity + '.admission.lock', exclusive=True, create=True):
                durable_name = 'maintenance.' + identity + '.json'
                if self.admin.exists(durable_name):
                    durable = _validate_state(self.admin.read(durable_name), identity)
                    if durable['closed'] and durable['request_id'] != request:
                        fail('installation_maintenance_held', 'another durable request owns closed admission')
                if self.leases.exists(identity + '.json'):
                    previous = _state(self.leases, identity)
                    if previous['closed'] and previous['request_id'] != request:
                        fail('installation_maintenance_held', 'another request owns closed admission')
                # Acquire first: if a supported writer is still running, refuse
                # without closing admission underneath its nested operations.
                self.stack.enter_context(self.leases.lock(identity + '.lock', exclusive=True, create=True))
                self._publish(identity, {'schema_version': 1, 'installation_id': identity,
                    'request_id': request, 'closed': True})
        except BlockingIOError:
            fail('installation_maintenance_busy', 'active operation leases must drain before maintenance')

    @contextmanager
    def exclusive(self, request):
        if os.geteuid() != 0:
            fail('managed_authority_required', 'managed lifecycle requires root authority')
        if not isinstance(request, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request):
            fail('installation_request_invalid', 'stable lifecycle request identity is required')
        if self.stack is not None:
            if self.request != request:
                fail('installation_request_conflict', 'nested lifecycle belongs to another request')
            yield self
            return
        try:
            with ExitStack() as stack:
                self.admin = stack.enter_context(RootDirectory(self.paths.admin_state, create=True))
                stack.enter_context(self.admin.lock('lifecycle.lock', exclusive=True, create=True))
                self.leases = stack.enter_context(RootDirectory(self.paths.lease_root, create=True, private=False))
                self.stack, self.request = stack, request
                self._close(self.identity, request)
                yield self
        except BlockingIOError:
            fail('installation_maintenance_busy', 'another administrator lifecycle operation is active')
        finally:
            self.stack, self.leases, self.admin = None, None, None

    def bind_initialized_identity(self, identity):
        if self.stack is None or not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
            fail('installation_maintenance_invalid', 'identity binding requires an active lifecycle transaction')
        if self.identity != identity:
            old = self.identity
            self._close(identity, self.request)
            self.identity = identity
            if old == 'fresh-managed':
                previous = _state(self.leases, old)
                self._publish(old, {**previous, 'closed': False})

    def writers_fenced(self):
        return self.stack is not None and self.fence is not None and self.fence.proven() is True

    def open_admission(self, *, request, verified):
        if verified is not True:
            fail('installation_activation_unverified', 'runtime acceptance is required before admission opens')
        with self.exclusive(request):
            previous = _state(self.leases, self.identity)
            self._publish(self.identity, {**previous, 'closed': False})


def prepare_start(installation_id, *, paths=ManagedPaths()):
    """Root supervisor recreates ephemeral anchors from durable admission only.

    This does not initialize installation identity or open admission. Missing
    durable authority refuses startup rather than guessing from service state.
    """
    if os.geteuid() != 0 or not isinstance(installation_id, str) or not re.fullmatch(r'[0-9a-f]{32}', installation_id):
        fail('managed_authority_required', 'root supervisor and a genuine installation identity are required')
    with RootDirectory(paths.admin_state) as admin:
        with admin.lock('lifecycle.lock', exclusive=True, create=True):
            value = admin.read('maintenance.' + installation_id + '.json')
            with RootDirectory(paths.lease_root, create=True, private=False) as leases:
                _validate_state(value, installation_id)
                with leases.lock(installation_id + '.lock', exclusive=True, create=True):
                    leases.write(installation_id + '.json', value, mode=0o644)
    return value
