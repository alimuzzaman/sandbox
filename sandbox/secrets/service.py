"""Transport-neutral, audit-first secret inspection and use orchestration."""
from __future__ import annotations

import secrets as secure_random
import string
from collections.abc import Callable, Sequence

from .audit import SecretAudit
from .models import MAX_SELECTED_KEYS, SecretBrokerError, UseProfile, success
from .parser import SecretParseError, parse_document
from .policy import fixed_mask, metadata, validate, validate_key
from .runner import run_with_secret
from .sources import SourceRegistry
from .writer import load_revision_key, opaque_revision, update_source


class SecretService:
    def __init__(self, registry: SourceRegistry, audit: SecretAudit, *, revision_key_path, use_profiles=None):
        self.registry = registry
        self.audit = audit
        self.revision_key_path = revision_key_path
        self.use_profiles = dict(use_profiles or {})

    def _authorize(self, source: str, mode: str, surface: str) -> None:
        if surface == "mcp" and mode not in self.registry.policy(source).mcp_modes:
            raise SecretBrokerError("source_mode_denied", "secret source mode is not authorized for MCP")

    def _document(self, source: str):
        safe = self.registry.read(source)
        try:
            return safe, parse_document(safe.content)
        except SecretParseError as exc:
            code = "duplicate_key" if exc.code == "duplicate_key" else "syntax_unsupported"
            raise SecretBrokerError(code, "registered secret source could not be parsed safely") from exc

    def _operate(self, operation, source, keys, surface, callback, *, profile=None, input_channel=None):
        correlation = self.audit.intent(operation, source, list(keys), surface=surface,
                                        profile=profile, input_channel=input_channel)
        try:
            result = callback(correlation)
        except SecretBrokerError as exc:
            self.audit.outcome(correlation, operation, source, list(keys), surface=surface,
                               decision="refused", reason_code=exc.code, profile=profile,
                               input_channel=input_channel)
            raise
        except Exception:
            self.audit.outcome(correlation, operation, source, list(keys), surface=surface,
                               decision="failed", reason_code="operation_failed", profile=profile,
                               input_channel=input_channel)
            raise
        revision = result.get("revision") if isinstance(result, dict) else None
        count = result.get("count") if isinstance(result, dict) else None
        self.audit.outcome(correlation, operation, source, list(keys), surface=surface,
                           decision="succeeded", profile=profile, revision=revision, count=count,
                           input_channel=input_channel)
        return result

    def inspect(self, source: str, *, keys: Sequence[str] | None = None,
                mode: str = "keys", exact_length: bool = False, surface: str = "cli") -> dict:
        selected = tuple(keys or ())
        if len(selected) > MAX_SELECTED_KEYS:
            raise SecretBrokerError("selection_too_large", "too many secret keys were selected")
        for key in selected:
            validate_key(key)
        if mode not in {"keys", "metadata", "masked"}:
            raise SecretBrokerError("mode_invalid", "secret inspection mode is invalid")
        if exact_length and (mode != "metadata" or len(selected) != 1):
            raise SecretBrokerError("mode_requires_one_key", "exact length requires one metadata key")
        if mode == "masked" and len(selected) != 1:
            raise SecretBrokerError("mode_requires_one_key", "masked inspection requires exactly one key")
        def perform(correlation):
            self._authorize(source, mode, surface)
            safe, document = self._document(source)
            if mode == "keys":
                names = sorted(document.entries)
                if selected:
                    names = [key for key in selected if key in document.entries]
                return success("keys", source=source, keys=names, count=len(names), correlation_id=correlation)
            entries = []
            for key in selected or tuple(sorted(document.entries)):
                record = document.entries.get(key)
                if record is None:
                    entries.append({"key": key, "state": "missing"})
                elif mode == "metadata":
                    entries.append(metadata(key, record.value, exact_length=exact_length))
                else:
                    entries.append(fixed_mask(key, record.value))
            return success(mode, source=source, entries=entries, count=len(entries),
                           correlation_id=correlation,
                           revision=opaque_revision(load_revision_key(self.revision_key_path), safe.content))
        return self._operate(mode, source, selected, surface, perform)

    def validate(self, source: str, key: str, profile: str, *, surface: str = "cli") -> dict:
        validate_key(key)
        def perform(correlation):
            self._authorize(source, "validate", surface)
            _, document = self._document(source)
            record = document.entries.get(key)
            if record is None:
                raise SecretBrokerError("key_missing", "secret key does not exist")
            return success("validate", source=source, validation=validate(profile, key, record.value),
                           correlation_id=correlation)
        return self._operate("validate", source, (key,), surface, perform, profile=profile)

    def run(self, source: str, key: str, argv: Sequence[str], *, destination: str,
            timeout_seconds: int = 300, max_output_bytes: int = 1_048_576,
            surface: str = "cli") -> dict:
        validate_key(key)
        if surface != "cli":
            raise SecretBrokerError("command_denied", "arbitrary secret commands are local CLI only")
        def perform(correlation):
            _, document = self._document(source)
            record = document.entries.get(key)
            if record is None:
                raise SecretBrokerError("key_missing", "secret key does not exist")
            result = run_with_secret(argv, destination=destination, value=record.value,
                                     timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes)
            return success("run", source=source, key=key, result=result.as_dict(), correlation_id=correlation)
        return self._operate("use", source, (key,), surface, perform)

    def use_profile(self, profile_name: str, *, surface: str = "mcp") -> dict:
        profile = self.use_profiles.get(profile_name)
        if profile is None:
            raise SecretBrokerError("profile_unknown", "secret use profile is not registered")
        def perform(correlation):
            if surface == "mcp":
                if not profile.mcp:
                    raise SecretBrokerError("profile_denied", "secret use profile is not authorized for MCP")
                self._authorize(profile.source, "use", surface)
            _, document = self._document(profile.source)
            record = document.entries.get(profile.key)
            if record is None:
                raise SecretBrokerError("key_missing", "secret key does not exist")
            result = run_with_secret(profile.argv, destination=profile.destination, value=record.value,
                                     timeout_seconds=profile.timeout_seconds,
                                     max_output_bytes=profile.max_output_bytes)
            return success("use", source=profile.source, profile=profile_name,
                           result=result.as_dict(), correlation_id=correlation)
        return self._operate("use", profile.source, (profile.key,), surface, perform, profile=profile_name)

    def set(self, source: str, key: str, value: str, *, intent="either",
            expected_revision=None, validation_profile=None, input_channel="tty",
            surface="cli") -> dict:
        validate_key(key)
        if surface != "cli":
            raise SecretBrokerError("update_denied", "secret updates require the local CLI")
        if not isinstance(value, str) or not value:
            raise SecretBrokerError("input_invalid", "secret input must be non-empty text")
        def perform(correlation):
            if validation_profile:
                checked = validate(validation_profile, key, value)
                if checked["syntax"] != "pass":
                    raise SecretBrokerError("shape_failed", "secret input failed the requested profile")
            else:
                checked = None
            safe = self.registry.read(source)
            action, revision = update_source(
                safe, key=key, value=value, revision_key=load_revision_key(self.revision_key_path),
                intent=intent, expected_revision=expected_revision,
            )
            return success("set", source=source, key=key, action=action, revision=revision,
                           validation=checked, correlation_id=correlation)
        return self._operate("set", source, (key,), surface, perform,
                             profile=validation_profile, input_channel=input_channel)

    def copy_reference(self, source: str, key: str, reference_source: str,
                       reference_key: str, **kwargs) -> dict:
        validate_key(key)
        validate_key(reference_key)
        if reference_source == source and reference_key == key:
            raise SecretBrokerError("input_invalid", "secret reference cannot target itself")
        if kwargs.get("surface", "cli") != "cli":
            raise SecretBrokerError("update_denied", "secret updates require the local CLI")
        intent = kwargs.get("intent", "either")
        expected_revision = kwargs.get("expected_revision")
        validation_profile = kwargs.get("validation_profile")
        def perform(correlation):
            _, reference_document = self._document(reference_source)
            record = reference_document.entries.get(reference_key)
            if record is None:
                raise SecretBrokerError("key_missing", "referenced secret key does not exist")
            checked = validate(validation_profile, key, record.value) if validation_profile else None
            if checked is not None and checked["syntax"] != "pass":
                raise SecretBrokerError("shape_failed", "referenced secret failed the requested profile")
            safe = self.registry.read(source)
            action, revision = update_source(
                safe, key=key, value=record.value,
                revision_key=load_revision_key(self.revision_key_path), intent=intent,
                expected_revision=expected_revision,
            )
            return success("set", source=source, key=key, action=action, revision=revision,
                           validation=checked, correlation_id=correlation)
        return self._operate("set", source, (key,), "cli", perform,
                             profile=validation_profile, input_channel="reference")

    def generate(self, source: str, key: str, profile: str, **kwargs) -> dict:
        if profile != "random-base64url-32-v1":
            raise SecretBrokerError("profile_unknown", "secret generation profile is not registered")
        alphabet = string.ascii_letters + string.digits + "-_"
        value = "".join(secure_random.choice(alphabet) for _ in range(43))
        return self.set(source, key, value, input_channel="generator", **kwargs)

    def reveal(self, source: str, key: str, consumer: Callable[[str], None], *,
               confirmed: bool, surface="cli") -> None:
        validate_key(key)
        if surface != "cli":
            raise SecretBrokerError("reveal_denied", "secret reveal is local human-only")
        def perform(correlation):
            if not confirmed:
                raise SecretBrokerError("confirmation_failed", "secret reveal confirmation did not match")
            _, document = self._document(source)
            record = document.entries.get(key)
            if record is None:
                raise SecretBrokerError("key_missing", "secret key does not exist")
            consumer(record.value)
            return success("reveal", source=source, key=key, correlation_id=correlation)
        self._operate("reveal", source, (key,), surface, perform)
