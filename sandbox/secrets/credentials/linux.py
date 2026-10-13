"""Linux Secret Service adapter that never unlocks, activates, or prompts."""

from __future__ import annotations

from contextlib import closing
import sys
from typing import Literal

from sandbox.secrets.models import CredentialLocator, CredentialState, InfisicalSourcePolicy

from .base import (
    CREDENTIAL_SERVICE,
    CredentialHandle,
    CredentialStoreError,
    locator_for_policy,
)


_BUS_NAME = "org.freedesktop.secrets"
_COLLECTION_PATH = "/org/freedesktop/secrets/aliases/default"


class LinuxSecretServiceCredentialStore:
    """Use only the current session's already-running Secret Service."""

    @staticmethod
    def _modules():
        if not sys.platform.startswith("linux"):
            raise CredentialStoreError("credential_backend_unavailable")
        try:
            import secretstorage
            from jeepney.bus_messages import message_bus
            from jeepney.io.blocking import Proxy
            from secretstorage.exceptions import (
                ItemNotFoundException,
                LockedException,
                SecretServiceNotAvailableException,
            )
            return (secretstorage, message_bus, Proxy, ItemNotFoundException,
                    LockedException, SecretServiceNotAvailableException)
        except ImportError:
            from sandbox.core._config import bootstrap_cli_dependencies
            if bootstrap_cli_dependencies():
                try:
                    import secretstorage
                    from jeepney.bus_messages import message_bus
                    from jeepney.io.blocking import Proxy
                    from secretstorage.exceptions import (
                        ItemNotFoundException,
                        LockedException,
                        SecretServiceNotAvailableException,
                    )
                    return (secretstorage, message_bus, Proxy, ItemNotFoundException,
                            LockedException, SecretServiceNotAvailableException)
                except Exception:
                    pass
            raise CredentialStoreError("credential_backend_unavailable") from None
        except Exception:
            raise CredentialStoreError("credential_backend_unavailable") from None

    def _open(self):
        (secretstorage, message_bus, Proxy, item_not_found, _locked,
         service_unavailable) = self._modules()
        try:
            connection = secretstorage.dbus_init()
        except Exception:
            raise CredentialStoreError("credential_backend_unavailable") from None
        try:
            # NameHasOwner does not activate an absent service. Never use
            # check_service_availability(), which also returns true for an
            # activatable (currently stopped) service.
            has_owner = Proxy(message_bus, connection).NameHasOwner(_BUS_NAME)[0] == 1
            if not has_owner:
                connection.close()
                raise CredentialStoreError("credential_backend_unavailable")
            # Constructing Collection checks the existing default object but
            # unlike get_default_collection() it will not create one.
            collection = secretstorage.Collection(connection, _COLLECTION_PATH)
            return secretstorage, connection, collection, item_not_found, service_unavailable
        except CredentialStoreError:
            raise
        except (item_not_found, service_unavailable):
            connection.close()
            raise CredentialStoreError("credential_backend_unavailable") from None
        except Exception:
            connection.close()
            raise CredentialStoreError("credential_backend_unavailable") from None

    @staticmethod
    def _attributes(locator: CredentialLocator) -> dict[str, str]:
        return {"application": CREDENTIAL_SERVICE, "account": locator.account}

    def _find(self, collection, locator: CredentialLocator):
        items = list(collection.search_items(self._attributes(locator)))
        if len(items) > 1:
            raise CredentialStoreError("credential_store_failed")
        return items[0] if items else None

    def status(self, policy: InfisicalSourcePolicy | object) -> CredentialState:
        try:
            locator = locator_for_policy(policy)
            secretstorage, connection, collection, _missing, _unavailable = self._open()
        except CredentialStoreError as exc:
            return (CredentialState.UNAVAILABLE if exc.code == "credential_backend_unavailable"
                    else CredentialState.FAILED)
        try:
            if collection.is_locked():
                return CredentialState.LOCKED
            return (CredentialState.PRESENT if self._find(collection, locator) is not None
                    else CredentialState.MISSING)
        except Exception:
            return CredentialState.FAILED
        finally:
            connection.close()

    def get(self, policy: InfisicalSourcePolicy | object) -> CredentialHandle:
        return CredentialHandle(self, locator_for_policy(policy))

    def put(
        self,
        policy: InfisicalSourcePolicy | object,
        value: str,
        *,
        intent: Literal["create", "replace"],
    ) -> None:
        if not isinstance(value, str) or not value or intent not in {"create", "replace"}:
            raise CredentialStoreError("credential_store_failed")
        locator = locator_for_policy(policy)
        secretstorage, connection, collection, _missing, _unavailable = self._open()
        try:
            if collection.is_locked():
                raise CredentialStoreError("credential_locked")
            existing = self._find(collection, locator)
            if intent == "create" and existing is not None:
                raise CredentialStoreError("credential_exists")
            if intent == "replace" and existing is None:
                raise CredentialStoreError("credential_missing")

            # Use SecretStorage's checked collection and secret formatting, but
            # do not call Collection.create_item(): that helper executes a
            # returned Secret Service prompt. A returned prompt path is refused
            # without invoking its Prompt method.
            from secretstorage.defines import SS_PREFIX
            from secretstorage.util import format_secret, open_session

            session = open_session(connection)
            secret = format_secret(session, value.encode("utf-8"), "text/plain")
            properties = {
                f"{SS_PREFIX}Item.Label": ("s", CREDENTIAL_SERVICE),
                f"{SS_PREFIX}Item.Attributes": ("a{ss}", self._attributes(locator)),
            }
            item_path, prompt = collection._collection.call(
                "CreateItem", "a{sv}(oayays)b", properties, secret,
                intent == "replace",
            )
            if len(item_path) <= 1 or prompt != "/":
                raise CredentialStoreError("credential_store_failed")
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialStoreError("credential_store_failed") from None
        finally:
            connection.close()

    def _read(self, locator: CredentialLocator) -> str:
        secretstorage, connection, collection, _missing, _unavailable = self._open()
        try:
            if collection.is_locked():
                raise CredentialStoreError("credential_locked")
            item = self._find(collection, locator)
            if item is None:
                raise CredentialStoreError("credential_missing")
            if item.is_locked():
                raise CredentialStoreError("credential_locked")
            raw = item.get_secret()
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                raise CredentialStoreError("credential_store_failed") from None
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialStoreError("credential_store_failed") from None
        finally:
            connection.close()
