"""Spec 067 credential stores, exercised only through synthetic OS-module fakes."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import hashlib
import sys
import time
from types import ModuleType
import unittest
from unittest import mock

from sandbox.secrets.credentials.base import (
    CREDENTIAL_SERVICE,
    CredentialStoreError,
    locator_for_policy,
)
from sandbox.secrets.credentials.linux import LinuxSecretServiceCredentialStore
from sandbox.secrets.credentials.macos import MacOSKeychainCredentialStore
from sandbox.secrets.credentials.manifest import create_credential_store
from sandbox.secrets.models import CredentialState, InfisicalSourcePolicy


POLICY = InfisicalSourcePolicy(
    alias="fixture-provider", endpoint="https://fixture.invalid", project_id="fixture-project",
    environment="development", path="/fixture", key="FIXTURE_KEY", client_id="fixture-client",
)
SYNTHETIC_VALUE = "synthetic-credential-one"
REPLACEMENT_VALUE = "synthetic-credential-two"


def _module(name: str, **attributes) -> ModuleType:
    module = ModuleType(name)
    module.__dict__.update(attributes)
    return module


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.started_at = time.monotonic()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(self._check_duration)
        # A regression must never reach a live OS store, subprocess, or terminal.
        for target in ("subprocess.Popen", "builtins.input", "getpass.getpass"):
            forbidden = self.stack.enter_context(mock.patch(
                target, side_effect=AssertionError("live operation forbidden in credential tests"),
            ))
            self.addCleanup(forbidden.assert_not_called)

    def _check_duration(self):
        self.assertLess(time.monotonic() - self.started_at, 5.0)

    def assert_code(self, code, operation):
        with self.assertRaises(CredentialStoreError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)
        self.assertFalse(caught.exception.retryable)
        self.assertNotIn(SYNTHETIC_VALUE, str(caught.exception.as_dict()))
        return caught.exception


class CredentialLocatorTests(CredentialTests):
    def test_locator_is_versioned_stable_and_shared_across_store_adapters(self):
        payload = b'{"client":"fixture-client","project":"fixture-project","source":"fixture-provider","version":1}'
        expected_account = "v1-" + hashlib.sha256(payload).hexdigest()
        locator = locator_for_policy(POLICY)
        self.assertEqual(locator.service, "sandbox.secret-manager.v1")
        self.assertEqual(locator.account, expected_account)
        self.assertEqual(locator, locator_for_policy({
            "alias": POLICY.alias, "projectId": POLICY.project_id, "clientId": POLICY.client_id,
        }))
        self.assertEqual(locator, locator_for_policy({
            "alias": POLICY.alias, "project_id": POLICY.project_id, "client_id": POLICY.client_id,
        }))
        self.assertEqual(MacOSKeychainCredentialStore().get(POLICY).locator, locator)
        self.assertEqual(LinuxSecretServiceCredentialStore().get(POLICY).locator, locator)

    def test_only_source_project_and_client_identity_change_locator(self):
        locator = locator_for_policy(POLICY)
        for field in ("alias", "project_id", "client_id"):
            with self.subTest(field=field):
                self.assertNotEqual(locator, locator_for_policy(replace(POLICY, **{field: "other"})))
        self.assertEqual(locator, locator_for_policy(replace(
            POLICY, endpoint="https://other.invalid", environment="production", path="/other",
            key="OTHER_KEY", timeout_seconds=20, mcp_modes=frozenset({"use"}),
        )))

    def test_incomplete_locator_identity_fails_before_store_access(self):
        for field in ("alias", "project_id", "client_id"):
            for value in ("", None, 12):
                with self.subTest(field=field, value=value):
                    self.assert_code("credential_store_failed", lambda: locator_for_policy(
                        replace(POLICY, **{field: value}),
                    ))

    def test_factory_selects_only_explicit_approved_platform_adapters(self):
        for platform, expected in (("darwin", MacOSKeychainCredentialStore),
                                   ("linux", LinuxSecretServiceCredentialStore),
                                   ("linux2", LinuxSecretServiceCredentialStore)):
            with self.subTest(platform=platform):
                self.assertIsInstance(create_credential_store(platform), expected)
                with mock.patch("sandbox.secrets.credentials.manifest.sys.platform", platform):
                    self.assertIsInstance(create_credential_store(), expected)
        with self.assertRaisesRegex(RuntimeError, "approved controller credential store"):
            create_credential_store("win32")


class MacOSCredentialTests(CredentialTests):
    def setUp(self):
        super().setUp()
        self.backend = mock.Mock(spec=["get_password", "set_password"])
        self.values = {}
        self.backend.get_password.side_effect = lambda service, account: self.values.get((service, account))
        self.backend.set_password.side_effect = lambda service, account, value: self.values.__setitem__((service, account), value)
        self.constructor = mock.Mock(return_value=self.backend)
        self.discovery = mock.Mock(side_effect=AssertionError("backend discovery forbidden"))
        keyring = _module("keyring", get_keyring=self.discovery, get_password=self.discovery,
                          set_password=self.discovery, set_keyring=self.discovery)
        backends = _module("keyring.backends")
        macos = _module("keyring.backends.macOS", Keyring=self.constructor)
        keyring.backends = backends
        backends.macOS = macos
        self.stack.enter_context(mock.patch.dict(sys.modules, {
            "keyring": keyring, "keyring.backends": backends, "keyring.backends.macOS": macos,
        }))
        self.stack.enter_context(mock.patch("sandbox.secrets.credentials.macos.sys.platform", "darwin"))
        self.addCleanup(self.discovery.assert_not_called)
        self.store = MacOSKeychainCredentialStore()

    def test_create_get_and_explicit_replace_use_exact_stable_locator(self):
        locator = locator_for_policy(POLICY)
        self.store.put(POLICY, SYNTHETIC_VALUE, intent="create")
        self.backend.set_password.assert_called_once_with(locator.service, locator.account, SYNTHETIC_VALUE)
        handle = self.store.get(POLICY)
        self.assertEqual(repr(handle), "<CredentialHandle>")
        self.assertEqual(handle.read(), SYNTHETIC_VALUE)
        self.assertEqual(handle.get_secret(), SYNTHETIC_VALUE.encode())
        self.store.put(POLICY, REPLACEMENT_VALUE, intent="replace")
        self.assertEqual(handle.read(), REPLACEMENT_VALUE)
        self.assertEqual(len(self.values), 1)

    def test_create_cannot_overwrite_and_replace_requires_existing_value(self):
        self.assert_code("credential_missing", lambda: self.store.put(POLICY, SYNTHETIC_VALUE, intent="replace"))
        self.backend.set_password.assert_not_called()
        self.store.put(POLICY, SYNTHETIC_VALUE, intent="create")
        self.assert_code("credential_exists", lambda: self.store.put(POLICY, REPLACEMENT_VALUE, intent="create"))
        self.assertEqual(self.store.get(POLICY).read(), SYNTHETIC_VALUE)
        self.assertEqual(self.backend.set_password.call_count, 1)

    def test_handle_lookup_is_lazy_and_missing_read_has_stable_error(self):
        handle = self.store.get(POLICY)
        self.constructor.assert_not_called()
        self.assert_code("credential_missing", handle.read)
        self.backend.get_password.assert_called_once()

    def test_handle_checks_deadline_before_and_after_value_lookup(self):
        self.store.put(POLICY, SYNTHETIC_VALUE, intent="create")
        self.backend.get_password.reset_mock()
        deadline = mock.Mock(spec=["check"])
        handle = self.store.get(POLICY)
        self.assertEqual(handle.get_secret(deadline), SYNTHETIC_VALUE.encode())
        self.assertEqual(deadline.check.call_count, 2)
        self.backend.get_password.reset_mock()
        deadline.check.side_effect = TimeoutError("synthetic expired deadline")
        with self.assertRaises(TimeoutError):
            handle.get_secret(deadline)
        self.backend.get_password.assert_not_called()

    def test_status_is_value_free_unknown_even_if_present_or_locked(self):
        locator = locator_for_policy(POLICY)
        for present in (False, True):
            with self.subTest(present=present):
                if present:
                    self.values[(locator.service, locator.account)] = SYNTHETIC_VALUE
                self.backend.get_password.side_effect = RuntimeError("synthetic locked keychain")
                self.assertEqual(self.store.status(POLICY), CredentialState.UNKNOWN)
        self.backend.get_password.assert_not_called()
        self.backend.set_password.assert_not_called()

    def test_wrong_platform_or_unavailable_backend_fails_without_fallback(self):
        with mock.patch("sandbox.secrets.credentials.macos.sys.platform", "linux"):
            self.assertEqual(self.store.status(POLICY), CredentialState.UNAVAILABLE)
            self.assert_code("credential_backend_unavailable", self.store.get(POLICY).read)
        self.constructor.assert_not_called()
        self.constructor.side_effect = RuntimeError(SYNTHETIC_VALUE)
        self.assertEqual(self.store.status(POLICY), CredentialState.UNAVAILABLE)
        self.assert_code("credential_backend_unavailable", self.store.get(POLICY).read)

    def test_locked_backend_or_write_failure_is_safe_and_never_retried(self):
        self.backend.get_password.side_effect = RuntimeError(SYNTHETIC_VALUE)
        error = self.assert_code("credential_store_failed", self.store.get(POLICY).read)
        self.assertTrue(error.__suppress_context__)
        self.assertEqual(self.backend.get_password.call_count, 1)
        self.backend.get_password.side_effect = None
        self.backend.get_password.return_value = None
        self.backend.set_password.side_effect = RuntimeError(SYNTHETIC_VALUE)
        self.assert_code("credential_store_failed", lambda: self.store.put(POLICY, SYNTHETIC_VALUE, intent="create"))
        self.assertEqual(self.backend.set_password.call_count, 1)

    def test_invalid_value_or_intent_never_reaches_backend(self):
        for value, intent in (("", "create"), (None, "create"), (12, "replace"),
                              (SYNTHETIC_VALUE, "automatic")):
            with self.subTest(intent=intent, value_type=type(value).__name__):
                self.assert_code("credential_store_failed", lambda: self.store.put(POLICY, value, intent=intent))
        self.constructor.assert_not_called()


class LinuxCredentialTests(CredentialTests):
    def setUp(self):
        super().setUp()
        self.items = []
        self.connections = []
        self.owner = True
        self.collection_locked = False
        self.prompt_path = "/"
        self.item_path = "/org/freedesktop/secrets/item/fixture"
        self.collection = mock.Mock()
        self.collection.is_locked.side_effect = lambda: self.collection_locked
        self.collection.search_items.side_effect = self._search
        self.collection._collection.call.side_effect = self._create
        self.forbidden = mock.Mock(side_effect=AssertionError("unlock, activation, or prompt forbidden"))
        self.collection.unlock = self.forbidden
        self.collection.create_item = self.forbidden
        self.secretstorage = _module("secretstorage", dbus_init=mock.Mock(side_effect=self._connect),
            Collection=mock.Mock(return_value=self.collection), unlock_objects=self.forbidden,
            get_default_collection=self.forbidden, create_collection=self.forbidden,
            check_service_availability=self.forbidden)
        self.proxy = mock.Mock()
        self.proxy.NameHasOwner.side_effect = lambda name: (int(self.owner),)
        self.proxy.StartServiceByName = self.forbidden
        self.proxy_constructor = mock.Mock(return_value=self.proxy)
        self.message_bus = object()
        self.open_session = mock.Mock(return_value=object())
        self.format_secret = mock.Mock(side_effect=lambda session, value, content_type: value)
        exceptions = _module("secretstorage.exceptions", ItemNotFoundException=type("ItemMissing", (Exception,), {}),
            LockedException=type("StoreLocked", (Exception,), {}),
            SecretServiceNotAvailableException=type("StoreUnavailable", (Exception,), {}))
        util = _module("secretstorage.util", open_session=self.open_session,
                       format_secret=self.format_secret, exec_prompt=self.forbidden,
                       unlock_objects=self.forbidden)
        modules = {
            "secretstorage": self.secretstorage, "secretstorage.exceptions": exceptions,
            "secretstorage.defines": _module("secretstorage.defines", SS_PREFIX="org.freedesktop.Secret."),
            "secretstorage.util": util,
            "jeepney": _module("jeepney"),
            "jeepney.bus_messages": _module("jeepney.bus_messages", message_bus=self.message_bus),
            "jeepney.io": _module("jeepney.io"),
            "jeepney.io.blocking": _module("jeepney.io.blocking", Proxy=self.proxy_constructor),
        }
        self.stack.enter_context(mock.patch.dict(sys.modules, modules))
        self.stack.enter_context(mock.patch("sandbox.secrets.credentials.linux.sys.platform", "linux"))
        self.addCleanup(self.forbidden.assert_not_called)
        self.addCleanup(self._assert_connections_closed)
        self.store = LinuxSecretServiceCredentialStore()

    def _connect(self):
        connection = mock.Mock(spec=["close"])
        self.connections.append(connection)
        return connection

    def _assert_connections_closed(self):
        for connection in self.connections:
            connection.close.assert_called_once_with()

    def _search(self, attributes):
        return [item for item in self.items if item.attributes == attributes]

    def _item(self, value=SYNTHETIC_VALUE.encode(), *, locked=False):
        item = mock.Mock()
        item.attributes = {"application": CREDENTIAL_SERVICE, "account": locator_for_policy(POLICY).account}
        item.is_locked.return_value = locked
        item.get_secret.return_value = value
        item.unlock = self.forbidden
        self.items.append(item)
        return item

    def _create(self, method, signature, properties, secret, replace_existing):
        self.assertEqual(method, "CreateItem")
        self.assertEqual(signature, "a{sv}(oayays)b")
        self.assertEqual(properties["org.freedesktop.Secret.Item.Label"], ("s", CREDENTIAL_SERVICE))
        attributes = properties["org.freedesktop.Secret.Item.Attributes"][1]
        if self.prompt_path == "/" and len(self.item_path) > 1:
            if replace_existing:
                self.items[:] = [item for item in self.items if item.attributes != attributes]
            self._item(secret)
        return self.item_path, self.prompt_path

    def test_create_read_and_replace_use_exact_attributes_and_existing_service(self):
        self.store.put(POLICY, SYNTHETIC_VALUE, intent="create")
        self.assertEqual(self.store.status(POLICY), CredentialState.PRESENT)
        handle = self.store.get(POLICY)
        self.assertEqual(handle.get_secret(), SYNTHETIC_VALUE.encode())
        self.store.put(POLICY, REPLACEMENT_VALUE, intent="replace")
        self.assertEqual(handle.read(), REPLACEMENT_VALUE)
        self.assertEqual(len(self.items), 1)
        self.assertEqual([call.args[-1] for call in self.collection._collection.call.call_args_list], [False, True])
        for call in self.proxy.NameHasOwner.call_args_list:
            self.assertEqual(call.args, ("org.freedesktop.secrets",))
        for call in self.secretstorage.Collection.call_args_list:
            self.assertEqual(call.args[1], "/org/freedesktop/secrets/aliases/default")

    def test_create_rejects_overwrite_and_replace_rejects_missing(self):
        self.assert_code("credential_missing", lambda: self.store.put(POLICY, SYNTHETIC_VALUE, intent="replace"))
        self.collection._collection.call.assert_not_called()
        self.store.put(POLICY, SYNTHETIC_VALUE, intent="create")
        self.assert_code("credential_exists", lambda: self.store.put(POLICY, REPLACEMENT_VALUE, intent="create"))
        self.assertEqual(self.store.get(POLICY).read(), SYNTHETIC_VALUE)
        self.assertEqual(self.collection._collection.call.call_count, 1)

    def test_missing_status_does_not_read_and_lazy_get_reports_missing(self):
        handle = self.store.get(POLICY)
        self.secretstorage.dbus_init.assert_not_called()
        self.assertEqual(self.store.status(POLICY), CredentialState.MISSING)
        self.assert_code("credential_missing", handle.read)
        self.open_session.assert_not_called()

    def test_present_status_does_not_read_secret_or_open_auth_session(self):
        item = self._item()
        self.assertEqual(self.store.status(POLICY), CredentialState.PRESENT)
        item.get_secret.assert_not_called()
        self.open_session.assert_not_called()

    def test_locked_collection_refuses_reads_and_writes_without_unlock(self):
        item = self._item()
        self.collection_locked = True
        self.assertEqual(self.store.status(POLICY), CredentialState.LOCKED)
        self.assert_code("credential_locked", self.store.get(POLICY).read)
        self.assert_code("credential_locked", lambda: self.store.put(POLICY, REPLACEMENT_VALUE, intent="replace"))
        self.collection.search_items.assert_not_called()
        self.collection._collection.call.assert_not_called()
        item.get_secret.assert_not_called()
        self.open_session.assert_not_called()

    def test_locked_item_refuses_read_without_unlock_or_value_access(self):
        item = self._item(locked=True)
        self.assert_code("credential_locked", self.store.get(POLICY).read)
        item.get_secret.assert_not_called()

    def test_stopped_service_is_unavailable_and_never_activated(self):
        self.owner = False
        self.assertEqual(self.store.status(POLICY), CredentialState.UNAVAILABLE)
        self.assert_code("credential_backend_unavailable", self.store.get(POLICY).read)
        self.assert_code("credential_backend_unavailable", lambda: self.store.put(POLICY, SYNTHETIC_VALUE, intent="create"))
        self.secretstorage.Collection.assert_not_called()
        self.open_session.assert_not_called()

    def test_missing_session_or_collection_is_unavailable_without_creation(self):
        self.secretstorage.dbus_init.side_effect = RuntimeError(SYNTHETIC_VALUE)
        self.assertEqual(self.store.status(POLICY), CredentialState.UNAVAILABLE)
        self.assert_code("credential_backend_unavailable", self.store.get(POLICY).read)
        self.secretstorage.Collection.assert_not_called()
        self.secretstorage.dbus_init.side_effect = self._connect
        missing = sys.modules["secretstorage.exceptions"].ItemNotFoundException
        self.secretstorage.Collection.side_effect = missing(SYNTHETIC_VALUE)
        self.assertEqual(self.store.status(POLICY), CredentialState.UNAVAILABLE)
        self.assert_code("credential_backend_unavailable", self.store.get(POLICY).read)

    def test_wrong_platform_never_opens_bus(self):
        with mock.patch("sandbox.secrets.credentials.linux.sys.platform", "darwin"):
            self.assertEqual(self.store.status(POLICY), CredentialState.UNAVAILABLE)
            self.assert_code("credential_backend_unavailable", self.store.get(POLICY).read)
        self.secretstorage.dbus_init.assert_not_called()

    def test_missing_dependency_is_unavailable_without_plaintext_fallback(self):
        with mock.patch.dict(sys.modules, {"secretstorage": None}):
            self.assertEqual(self.store.status(POLICY), CredentialState.UNAVAILABLE)
            self.assert_code("credential_backend_unavailable", self.store.get(POLICY).read)
        self.secretstorage.dbus_init.assert_not_called()

    def test_returned_prompt_is_refused_without_execution_or_retry(self):
        self.prompt_path = "/org/freedesktop/secrets/prompt/fixture"
        self.assert_code("credential_store_failed", lambda: self.store.put(POLICY, SYNTHETIC_VALUE, intent="create"))
        self.assertEqual(self.collection._collection.call.call_count, 1)
        self.assertEqual(self.items, [])

    def test_empty_creation_path_fails_without_retry(self):
        self.item_path = "/"
        self.assert_code("credential_store_failed", lambda: self.store.put(POLICY, SYNTHETIC_VALUE, intent="create"))
        self.assertEqual(self.collection._collection.call.call_count, 1)
        self.assertEqual(self.items, [])

    def test_duplicate_matches_fail_without_read_or_write(self):
        items = [self._item(), self._item()]
        self.assertEqual(self.store.status(POLICY), CredentialState.FAILED)
        self.assert_code("credential_store_failed", self.store.get(POLICY).read)
        self.assert_code("credential_store_failed", lambda: self.store.put(POLICY, REPLACEMENT_VALUE, intent="replace"))
        for item in items:
            item.get_secret.assert_not_called()
        self.collection._collection.call.assert_not_called()

    def test_backend_error_or_invalid_utf8_is_sanitized_and_not_retried(self):
        item = self._item()
        item.get_secret.side_effect = RuntimeError(SYNTHETIC_VALUE)
        self.assert_code("credential_store_failed", self.store.get(POLICY).read)
        self.assertEqual(item.get_secret.call_count, 1)
        item.get_secret.side_effect = None
        item.get_secret.return_value = b"\xff"
        self.assert_code("credential_store_failed", self.store.get(POLICY).read)

    def test_invalid_input_never_opens_bus(self):
        for value, intent in (("", "create"), (None, "create"), (12, "replace"),
                              (SYNTHETIC_VALUE, "automatic")):
            with self.subTest(intent=intent, value_type=type(value).__name__):
                self.assert_code("credential_store_failed", lambda: self.store.put(POLICY, value, intent=intent))
        self.secretstorage.dbus_init.assert_not_called()


if __name__ == "__main__":
    unittest.main()
