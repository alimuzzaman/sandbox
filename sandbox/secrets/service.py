"""Transport-neutral, audit-first secret inspection and use orchestration."""
from __future__ import annotations

import secrets as secure_random
import string
from collections.abc import Callable, Sequence

from .audit import SecretAudit
from .formats import SecretFormatError, parse_secret_document, validate_selector
from .models import MAX_SELECTED_KEYS, SecretBrokerError, UseProfile, success
from .parser import SecretParseError, parse_document
from .policy import fixed_mask, length_bucket, metadata, validate, validate_key
from .runner import run_with_secret, run_with_secrets
from .sources import SourceRegistry
from .organizer import organize as organize_document
from .writer import load_revision_key, opaque_revision, rewrite_source, update_source


class GHCRStagingCredentialAdapter:
    """Fixed repository-read broker adapter for Feature 050.

    This adapter never returns plaintext.  It issues one ``BrokerLease`` and
    invokes its consume callback for the exact measured staging recipient.
    """

    def __init__(self, resolver, binding, *, recipient: str,
                 credential_reference_revision: str,
                 revision_key: bytes) -> None:
        from sandbox.isolation.credential_binding import CredentialBinding
        if type(binding) is not CredentialBinding:
            raise SecretBrokerError("binding_invalid", "staging credential binding is invalid")
        if not isinstance(recipient, str) or not recipient.startswith("ghcr-repository-read:"):
            raise SecretBrokerError("destination_denied", "staging recipient is invalid")
        if not isinstance(credential_reference_revision, str) or not credential_reference_revision:
            raise SecretBrokerError("binding_invalid", "credential reference revision is invalid")
        if not isinstance(revision_key, bytes) or len(revision_key) != 32:
            raise SecretBrokerError("binding_invalid", "credential revision key is invalid")
        self._resolver = resolver
        self._binding = binding
        self.recipient = recipient
        self.credential_reference_revision = credential_reference_revision
        self._revision_key = revision_key

    @property
    def binding_id(self) -> str:
        return self._binding.binding_id

    @property
    def binding_version(self) -> int:
        return self._binding.version

    def prepare_for_stage(self, *, recipient: str, binding_id: str,
                          binding_version: int):
        if recipient != self.recipient or binding_id != self.binding_id \
                or binding_version != self.binding_version:
            raise SecretBrokerError("binding_invalid", "staging broker authority changed")
        return self._resolver.issue_revision_bound(
            self._binding, expected_revision=self.credential_reference_revision,
            revision_key=self._revision_key)

    def consume_for_stage(self, *, recipient: str, binding_id: str,
                          binding_version: int, consumer: Callable[[bytes], object]):
        return self.prepare_for_stage(recipient=recipient, binding_id=binding_id,
                                      binding_version=binding_version).consume(consumer)


def _bounded_call(callback):
    """Return a result or a newly allocated public error with no traceback chain.

    Secret-bearing parser and subprocess exceptions can retain their complete
    input in attributes even when their rendered message looks harmless.  The
    public error is therefore created here and raised only after this exception
    handler has returned, so neither ``__context__`` nor ``__cause__`` can point
    back to the original exception or its frame locals.
    """
    try:
        return callback(), None
    except SecretBrokerError as exc:
        public = (exc.code, exc.message, exc.retryable)
    except Exception:
        public = ("operation_failed", "secret operation failed", False)
    return None, SecretBrokerError(public[0], public[1], retryable=public[2])


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
            document = (
                parse_document(safe.content)
                if safe.policy.format == "dotenv"
                else parse_secret_document(safe.content, safe.policy.format)
            )
            return safe, document
        except (SecretParseError, SecretFormatError) as exc:
            code = "duplicate_key" if exc.code == "duplicate_key" else "syntax_unsupported"
            raise SecretBrokerError(code, "registered secret source could not be parsed safely") from exc

    def _validate_source_key(self, source: str, key: str) -> str:
        if self.registry.policy(source).format == "dotenv":
            return validate_key(key)
        try:
            return validate_selector(key)
        except SecretFormatError:
            raise SecretBrokerError("key_invalid", "secret key selector is invalid") from None

    @staticmethod
    def _entry_value(record) -> str:
        if record.value is None:
            raise SecretBrokerError("value_unavailable", "secret value is not available for this format")
        return record.value

    @staticmethod
    def _entry_metadata(key: str, record, *, exact_length: bool) -> dict:
        if exact_length and not getattr(record, "allow_exact_length", True):
            raise SecretBrokerError(
                "exact_length_denied", "exact length is not available for this secret format",
            )
        value = getattr(record, "value", None)
        if value is None:
            size = getattr(record, "byte_length", 0) or 0
            return {
                "key": key, "state": "present",
                "kind": getattr(record, "kind_hint", None) or "binary",
                "length_bucket": length_bucket(size),
            }
        result = metadata(key, value, exact_length=exact_length)
        hint = getattr(record, "kind_hint", None)
        if hint:
            result["kind"] = hint
        return result

    def _operate(self, operation, source, keys, surface, callback, *, profile=None, input_channel=None):
        correlation, failure = _bounded_call(lambda: self.audit.intent(
            operation, source, list(keys), surface=surface,
            profile=profile, input_channel=input_channel,
        ))
        if failure is not None:
            raise failure

        result, failure = _bounded_call(lambda: callback(correlation))
        if failure is not None:
            decision = "refused" if failure.code != "operation_failed" else "failed"
            _, audit_failure = _bounded_call(lambda: self.audit.outcome(
                correlation, operation, source, list(keys), surface=surface,
                decision=decision, reason_code=failure.code, profile=profile,
                input_channel=input_channel,
            ))
            if audit_failure is not None:
                raise audit_failure
            raise failure
        revision = result.get("revision") if isinstance(result, dict) else None
        count = result.get("count") if isinstance(result, dict) else None
        _, failure = _bounded_call(lambda: self.audit.outcome(
            correlation, operation, source, list(keys), surface=surface,
            decision="succeeded", profile=profile, revision=revision, count=count,
            input_channel=input_channel,
        ))
        if failure is not None:
            raise failure
        return result

    def inspect(self, source: str, *, keys: Sequence[str] | None = None,
                mode: str = "keys", exact_length: bool = False, surface: str = "cli") -> dict:
        selected = tuple(keys or ())
        if len(selected) > MAX_SELECTED_KEYS:
            raise SecretBrokerError("selection_too_large", "too many secret keys were selected")
        for key in selected:
            self._validate_source_key(source, key)
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
                    entries.append(self._entry_metadata(key, record, exact_length=exact_length))
                else:
                    if not getattr(record, "allow_mask", True):
                        raise SecretBrokerError(
                            "mask_denied", "masking is not available for this secret format",
                        )
                    entries.append(fixed_mask(key, self._entry_value(record)))
            return success(mode, source=source, entries=entries, count=len(entries),
                           correlation_id=correlation,
                           revision=opaque_revision(load_revision_key(self.revision_key_path), safe.content))
        return self._operate(mode, source, selected, surface, perform)

    def source_info(self, source: str, *, exact_size: bool = False,
                    surface: str = "cli") -> dict:
        if exact_size and surface != "cli":
            raise SecretBrokerError(
                "exact_size_denied", "exact source size is available only to the local CLI",
            )

        def perform(correlation):
            self._authorize(source, "source_info", surface)
            details = self.registry.probe(source, exact_size=exact_size)
            return success("source_info", **details, correlation_id=correlation)

        return self._operate("source_info", source, (), surface, perform)

    def validate(self, source: str, key: str, profile: str, *, surface: str = "cli") -> dict:
        self._validate_source_key(source, key)
        def perform(correlation):
            self._authorize(source, "validate", surface)
            _, document = self._document(source)
            record = document.entries.get(key)
            if record is None:
                raise SecretBrokerError("key_missing", "secret key does not exist")
            return success("validate", source=source, validation=validate(
                profile, key, self._entry_value(record),
            ),
                           correlation_id=correlation)
        return self._operate("validate", source, (key,), surface, perform, profile=profile)

    def run(self, source: str, key: str, argv: Sequence[str], *, destination: str,
            timeout_seconds: int = 300, max_output_bytes: int = 1_048_576,
            surface: str = "cli") -> dict:
        return self.run_many(
            source, ((key, destination),), argv,
            timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes,
            surface=surface,
        )

    def run_many(self, source: str, bindings: Sequence[tuple[str, str]],
                 argv: Sequence[str], *, timeout_seconds: int = 300,
                 max_output_bytes: int = 1_048_576, surface: str = "cli") -> dict:
        if not isinstance(bindings, (list, tuple)) or not bindings:
            raise SecretBrokerError("selection_invalid", "at least one secret binding is required")
        if len(bindings) > MAX_SELECTED_KEYS:
            raise SecretBrokerError("selection_too_large", "too many secret keys were selected")
        from .policy import validate_destination
        normalized: list[tuple[str, str]] = []
        destinations: set[str] = set()
        for binding in bindings:
            if not isinstance(binding, (list, tuple)) or len(binding) != 2:
                raise SecretBrokerError("selection_invalid", "secret bindings must pair a key with a destination")
            key, destination = binding
            self._validate_source_key(source, key)
            if not isinstance(destination, str):
                raise SecretBrokerError("destination_denied", "secret destination is invalid")
            validate_destination(destination)
            if destination in destinations:
                raise SecretBrokerError("destination_denied", "secret destinations must be unique")
            destinations.add(destination)
            normalized.append((key, destination))
        keys = tuple(key for key, _destination in normalized)
        if len(set(keys)) != len(keys):
            raise SecretBrokerError("selection_invalid", "secret keys must be unique")
        if surface != "cli":
            raise SecretBrokerError("command_denied", "arbitrary secret commands are local CLI only")
        def perform(correlation):
            _, document = self._document(source)
            values = {}
            for key, destination in normalized:
                record = document.entries.get(key)
                if record is None:
                    raise SecretBrokerError("key_missing", "secret key does not exist")
                values[destination] = self._entry_value(record)
            result = run_with_secrets(
                argv, secrets=values, timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
            )
            payload = {"source": source, "result": result.as_dict(),
                       "correlation_id": correlation}
            if len(keys) == 1:
                payload["key"] = keys[0]
            else:
                payload["keys"] = list(keys)
            return success("run", **payload)
        return self._operate("use", source, keys, surface, perform)

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
            result = run_with_secret(
                profile.argv, destination=profile.destination, value=self._entry_value(record),
                                     timeout_seconds=profile.timeout_seconds,
                                     max_output_bytes=profile.max_output_bytes)
            return success("use", source=profile.source, profile=profile_name,
                           result=result.as_dict(), correlation_id=correlation)
        return self._operate("use", profile.source, (profile.key,), surface, perform, profile=profile_name)

    def set(self, source: str, key: str, value: str, *, intent="either",
            expected_revision=None, validation_profile=None, input_channel="tty",
            surface="cli") -> dict:
        self._validate_source_key(source, key)
        if surface != "cli":
            raise SecretBrokerError("update_denied", "secret updates require the local CLI")
        if not isinstance(value, str) or not value:
            raise SecretBrokerError("input_invalid", "secret input must be non-empty text")
        def perform(correlation):
            if self.registry.policy(source).format != "dotenv":
                raise SecretBrokerError(
                    "update_unsupported", "secret updates are not supported for this format",
                )
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

    def organize(self, source: str, *, apply: bool = False, expected_revision=None,
                 surface: str = "cli") -> dict:
        """Group one dotenv source into documented sections without reading values."""
        if surface != "cli":
            raise SecretBrokerError("organize_denied", "secret organization requires the local CLI")

        def perform(correlation):
            if self.registry.policy(source).format != "dotenv":
                raise SecretBrokerError(
                    "organize_unsupported", "secret organization supports dotenv sources only",
                )
            safe, document = self._document(source)
            try:
                report = organize_document(document)
            except SecretParseError as exc:
                code = exc.code if exc.code in {"mixed_newlines", "round_trip_failed"} else "syntax_unsupported"
                raise SecretBrokerError(code, "secret source could not be organized safely") from exc
            revision_key = load_revision_key(self.revision_key_path)
            revision = opaque_revision(revision_key, safe.content)
            if apply and report.changed:
                revision = rewrite_source(safe, report.content, revision_key=revision_key,
                                          expected_revision=expected_revision)
            return success("organize", source=source, applied=bool(apply and report.changed),
                           changed=report.changed, count=report.count,
                           groups=[{"title": title, "keys": keys} for title, keys in report.groups],
                           revision=revision, correlation_id=correlation)

        return self._operate("organize", source, (), surface, perform)

    def copy_reference(self, source: str, key: str, reference_source: str,
                       reference_key: str, **kwargs) -> dict:
        self._validate_source_key(source, key)
        self._validate_source_key(reference_source, reference_key)
        if reference_source == source and reference_key == key:
            raise SecretBrokerError("input_invalid", "secret reference cannot target itself")
        if kwargs.get("surface", "cli") != "cli":
            raise SecretBrokerError("update_denied", "secret updates require the local CLI")
        intent = kwargs.get("intent", "either")
        expected_revision = kwargs.get("expected_revision")
        validation_profile = kwargs.get("validation_profile")
        def perform(correlation):
            if self.registry.policy(source).format != "dotenv":
                raise SecretBrokerError(
                    "update_unsupported", "secret updates are not supported for this format",
                )
            _, reference_document = self._document(reference_source)
            record = reference_document.entries.get(reference_key)
            if record is None:
                raise SecretBrokerError("key_missing", "referenced secret key does not exist")
            checked = validate(
                validation_profile, key, self._entry_value(record),
            ) if validation_profile else None
            if checked is not None and checked["syntax"] != "pass":
                raise SecretBrokerError("shape_failed", "referenced secret failed the requested profile")
            safe = self.registry.read(source)
            action, revision = update_source(
                safe, key=key, value=self._entry_value(record),
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
        self._validate_source_key(source, key)
        if surface != "cli":
            raise SecretBrokerError("reveal_denied", "secret reveal is local human-only")
        def perform(correlation):
            if not confirmed:
                raise SecretBrokerError("confirmation_failed", "secret reveal confirmation did not match")
            _, document = self._document(source)
            record = document.entries.get(key)
            if record is None:
                raise SecretBrokerError("key_missing", "secret key does not exist")
            consumer(self._entry_value(record))
            return success("reveal", source=source, key=key, correlation_id=correlation)
        self._operate("reveal", source, (key,), surface, perform)
