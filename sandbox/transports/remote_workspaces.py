"""Strict, path-free control-plane transport for durable remote workspaces.

The workspace index is owned by the selected remote Sandbox controller.  This
adapter deliberately does not resolve project directories, inspect Docker, or
own deployment state.  Callers inject the remote runner and, for creation, the
checkout deployment/registration seams.  Every control operation therefore
crosses the boundary using an opaque project identity or durable workspace/
migration-plan identifier rather than a local filesystem path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import shlex
from typing import Any, Callable, Mapping

from sandbox.services.redaction import redact_structure, redact_text


_MAX_REMOTE_JSON_BYTES = 1_048_576
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


class RemoteWorkspaceError(RuntimeError):
    """Stable failure raised by the workspace control-plane boundary."""

    def __init__(self, code: str, message: str, *, detail: str | None = None) -> None:
        if not isinstance(code, str) or not _SAFE_ERROR_CODE.fullmatch(code):
            code = "workspace_remote_failed"
        self.code = code
        self.detail = _safe_detail(detail or message)
        self.message = self.detail or _safe_detail(message) or "remote workspace operation failed"
        super().__init__(f"{code}: {self.message}")


@dataclass(frozen=True)
class WorkspaceCreateRequest:
    """Validated creation input passed to injected checkout seams.

    ``checkout_locator`` is intentionally retained only inside this object.  A
    deployment or registration callback may use it to prepare/register an
    exact checkout, but the control-plane command builder never serializes it.
    """

    remote_name: str
    project_identity: str
    workspace_label: str
    mode: str = "reusable"
    checkout_locator: str | None = None
    registration: Mapping[str, Any] = field(default_factory=dict)


def _safe_detail(value: object, *, limit: int = 1024) -> str:
    """Bound remote diagnostics without forwarding credentials."""
    if not isinstance(value, str):
        return ""
    return redact_text(value.strip())[-limit:]


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise RemoteWorkspaceError("workspace_identity_invalid", f"{label} is invalid")
    # The grammar above already excludes path separators. Keep this explicit so
    # a future grammar change cannot accidentally turn an opaque ID into a path.
    if "/" in value or "\\" in value:
        raise RemoteWorkspaceError("workspace_identity_invalid", f"{label} must be path-free")
    return value


def _safe_label(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_LABEL.fullmatch(value):
        raise RemoteWorkspaceError("workspace_identity_invalid", f"{label} is invalid")
    return value


def _positive_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5000:
        raise RemoteWorkspaceError("workspace_request_invalid", "limit must be between 1 and 5000")
    return value


def _non_negative_generation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RemoteWorkspaceError("workspace_request_invalid", "index_generation must be non-negative")
    return value


def _last_result_field(result: object, name: str) -> object:
    return getattr(result, name, "")


def _parse_envelope(stdout: object) -> dict[str, Any]:
    """Parse exactly one bounded top-level JSON object.

    Unlike the historical job transport, workspace control does not scan for a
    last JSON line.  A warning, nested envelope, or multiple documents makes
    the response ambiguous and is rejected as a protocol error.
    """
    if not isinstance(stdout, str):
        raise RemoteWorkspaceError("workspace_protocol_invalid", "remote response is not text")
    if len(stdout.encode("utf-8", errors="replace")) > _MAX_REMOTE_JSON_BYTES:
        raise RemoteWorkspaceError("workspace_protocol_invalid", "remote response is too large")
    raw = stdout.strip()
    if not raw:
        raise RemoteWorkspaceError("workspace_protocol_invalid", "remote response is empty")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # JSONDecodeError retains the complete source document in ``doc``.
        raise RemoteWorkspaceError(
            "workspace_protocol_invalid", "remote response is not JSON"
        ) from None
    if not isinstance(payload, dict) or payload.get("ok") is not True and payload.get("ok") is not False:
        raise RemoteWorkspaceError(
            "workspace_protocol_invalid", "remote response must be a top-level ok object"
        )
    # Workspace status records include an ``error`` field for their own
    # nullable state, so ``error: null`` is part of a valid successful record.
    # A populated error on an ``ok`` envelope remains malformed; failures must
    # use ``ok: false`` so callers cannot mistake an error for a healthy state.
    if payload.get("ok") is True and payload.get("error") is not None:
        raise RemoteWorkspaceError(
            "workspace_protocol_invalid", "successful remote response cannot contain error"
        )
    sanitized = redact_structure(payload)
    if not isinstance(sanitized, dict):
        raise RemoteWorkspaceError("workspace_protocol_invalid", "remote response redaction failed")
    return sanitized


def _remote_error(payload: Mapping[str, Any]) -> RemoteWorkspaceError:
    raw_error = payload.get("error")
    if isinstance(raw_error, Mapping):
        raw_code = raw_error.get("code")
        raw_message = (
            raw_error.get("message") or raw_error.get("detail")
            or payload.get("message") or raw_code
        )
    else:
        # The canonical controller failure envelope is top-level
        # ``{"ok":false,"code":"...","message":"..."}``.  Accept the
        # older ``error`` spelling as a compatibility form, but never discard
        # the structured top-level fields.
        raw_code = payload.get("code") or raw_error
        raw_message = payload.get("message") or raw_error or raw_code
    code = raw_code if isinstance(raw_code, str) and _SAFE_ERROR_CODE.fullmatch(raw_code) else "workspace_remote_failed"
    message = _safe_detail(raw_message if isinstance(raw_message, str) else "remote operation failed")
    return RemoteWorkspaceError(code, message or "remote operation failed")


def _success(payload: dict[str, Any], operation: str) -> dict[str, Any]:
    if payload.get("ok") is not True:
        raise _remote_error(payload)
    # Do not silently unwrap ``data`` or fabricate a successful response. The
    # caller receives the exact feature-owned envelope from the remote.
    if not isinstance(payload, dict):  # defensive; _parse_envelope already checks
        raise RemoteWorkspaceError("workspace_protocol_invalid", f"{operation} response is invalid")
    sanitized = redact_structure(payload)
    if not isinstance(sanitized, dict):
        raise RemoteWorkspaceError("workspace_protocol_invalid", f"{operation} response redaction failed")
    return sanitized


class RemoteWorkspaceTransport:
    """Exchange strict JSON workspace-control envelopes with a remote.

    ``ssh_run`` is an injected bounded runner with the same shape as the
    existing remote transport: ``ssh_run(remote_record, shell_command,
    timeout=seconds)``.  The adapter never imports SSH/Docker or resolves a
    project path.  ``deploy`` and ``register`` are optional creation seams and
    receive :class:`WorkspaceCreateRequest` (and, for registration, the
    deployment result) rather than being mixed into control commands.
    """

    def __init__(
        self,
        *,
        remote_lookup: Callable[[str], Mapping[str, Any] | None],
        ssh_run: Callable[..., object],
        deploy: Callable[[WorkspaceCreateRequest], Mapping[str, Any] | None] | None = None,
        register: Callable[
            [WorkspaceCreateRequest, Mapping[str, Any] | None], Mapping[str, Any] | None
        ] | None = None,
        remote_sb_path: Callable[[Mapping[str, Any]], str] | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        if not callable(remote_lookup) or not callable(ssh_run):
            raise TypeError("remote_lookup and ssh_run are required callables")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive integer")
        self.remote_lookup = remote_lookup
        self.ssh_run = ssh_run
        self.deploy = deploy
        self.register = register
        self.remote_sb_path = remote_sb_path or (lambda _remote: "sb")
        self.timeout_seconds = timeout_seconds

    def _remote(self, remote_name: str) -> Mapping[str, Any]:
        if not isinstance(remote_name, str) or not remote_name.strip():
            raise RemoteWorkspaceError("workspace_remote_unavailable", "remote name is invalid")
        remote = self.remote_lookup(remote_name)
        if not isinstance(remote, Mapping) or not remote.get("provisioned"):
            raise RemoteWorkspaceError("workspace_remote_unavailable", "remote is unknown or unprovisioned")
        return remote

    def _command(self, remote: Mapping[str, Any], argv: list[str]) -> str:
        if any(item in {"--project-dir", "--path", "--workspace-path", "--checkout-path"} for item in argv):
            raise RemoteWorkspaceError("workspace_protocol_invalid", "workspace control cannot send filesystem paths")
        command = [self.remote_sb_path(remote), *argv, "--json"]
        return shlex.join(command)

    def _control(self, remote_name: str, argv: list[str], *, timeout: int | None = None) -> dict[str, Any]:
        remote = self._remote(remote_name)
        command = self._command(remote, argv)
        try:
            result = self.ssh_run(remote, command, timeout=timeout or self.timeout_seconds)
        except RemoteWorkspaceError:
            raise
        except Exception as exc:  # injected runner failures have one stable code
            detail = _safe_detail(str(exc))
            raise RemoteWorkspaceError(
                "workspace_remote_failed", detail or "remote runner failed"
            ) from None
        return_code = _last_result_field(result, "returncode")
        stdout = _last_result_field(result, "stdout")
        stderr = _last_result_field(result, "stderr")
        try:
            payload = _parse_envelope(stdout)
        except RemoteWorkspaceError as parse_error:
            # Some bounded runners preserve a controller's structured failure
            # on stderr while stdout is empty (or contains a shell diagnostic).
            # Decode that envelope before collapsing the failure to a generic
            # transport error; stderr is still sanitized by _remote_error.
            try:
                stderr_payload = _parse_envelope(stderr)
            except RemoteWorkspaceError:
                stderr_payload = None
            if stderr_payload is not None and stderr_payload.get("ok") is False:
                raise _remote_error(stderr_payload) from parse_error
            if return_code not in (0, None):
                detail = _safe_detail(stderr) or parse_error.detail
                raise RemoteWorkspaceError("workspace_remote_failed", detail or "remote command failed") from parse_error
            raise
        if return_code not in (0, None) or payload.get("ok") is not True:
            raise _remote_error(payload) if payload.get("ok") is False else RemoteWorkspaceError(
                "workspace_remote_failed", _safe_detail(stderr) or "remote command failed"
            )
        return _success(payload, "workspace control")

    @staticmethod
    def _project_identity(value: object) -> str:
        return _safe_id(value, "project_identity")

    @staticmethod
    def _workspace_id(value: object) -> str:
        return _safe_id(value, "workspace_id")

    @staticmethod
    def _plan_id(value: object) -> str:
        return _safe_id(value, "plan_id")

    def list(
        self,
        remote_name: str,
        *,
        project_identity: str | None = None,
        workspace_id: str | None = None,
        limit: int = 50,
        active_only: bool = False,
        measure_sizes: bool = False,
    ) -> dict[str, Any]:
        args = ["workspace", "list", "--limit", str(_positive_limit(limit))]
        if project_identity is not None:
            args += ["--project-identity", self._project_identity(project_identity)]
        if workspace_id is not None:
            args += ["--workspace-id", self._workspace_id(workspace_id)]
        if not isinstance(active_only, bool):
            raise RemoteWorkspaceError("workspace_request_invalid", "active_only must be boolean")
        if active_only:
            args.append("--active-only")
        if not isinstance(measure_sizes, bool):
            raise RemoteWorkspaceError("workspace_request_invalid", "measure_sizes must be boolean")
        if measure_sizes:
            args.append("--measure-sizes")
        return self._control(remote_name, args)

    def status(
        self,
        remote_name: str,
        workspace_id: str,
        *,
        project_identity: str | None = None,
    ) -> dict[str, Any]:
        args = ["workspace", "status", "--workspace-id", self._workspace_id(workspace_id)]
        if project_identity is not None:
            args += ["--project-identity", self._project_identity(project_identity)]
        return self._control(remote_name, args)

    def migration_plan(
        self,
        remote_name: str,
        project_identity: str,
        *,
        inventory_digest: str | None = None,
        index_generation: int | None = None,
    ) -> dict[str, Any]:
        args = ["workspace", "migrate", "--project-identity", self._project_identity(project_identity)]
        if inventory_digest is not None:
            args += ["--inventory-digest", self._safe_digest(inventory_digest)]
        if index_generation is not None:
            args += ["--index-generation", str(_non_negative_generation(index_generation))]
        return self._control(remote_name, args)

    def migration_apply(
        self,
        remote_name: str,
        plan_id: str,
        *,
        confirm: bool = False,
        project_identity: str | None = None,
    ) -> dict[str, Any]:
        if confirm is not True:
            raise RemoteWorkspaceError("confirmation_required", "workspace migration apply requires confirmation")
        args = ["workspace", "migrate", "--plan-id", self._plan_id(plan_id), "--confirm"]
        if project_identity is not None:
            args += ["--project-identity", self._project_identity(project_identity)]
        return self._control(remote_name, args)

    def reset(
        self,
        remote_name: str,
        workspace_id: str,
        *,
        confirm: bool = False,
        project_identity: str | None = None,
    ) -> dict[str, Any]:
        return self._destructive(remote_name, "reset", workspace_id, confirm=confirm,
                                 project_identity=project_identity)

    def destroy(
        self,
        remote_name: str,
        workspace_id: str,
        *,
        confirm: bool = False,
        project_identity: str | None = None,
    ) -> dict[str, Any]:
        return self._destructive(remote_name, "destroy", workspace_id, confirm=confirm,
                                 project_identity=project_identity)

    def _destructive(
        self,
        remote_name: str,
        action: str,
        workspace_id: str,
        *,
        confirm: bool,
        project_identity: str | None,
    ) -> dict[str, Any]:
        if confirm is not True:
            raise RemoteWorkspaceError("confirmation_required", f"workspace {action} requires confirmation")
        if action not in {"reset", "destroy"}:
            raise ValueError("unsupported destructive workspace action")
        args = ["workspace", action, "--workspace-id", self._workspace_id(workspace_id), "--confirm"]
        if project_identity is not None:
            args += ["--project-identity", self._project_identity(project_identity)]
        return self._control(remote_name, args)

    @staticmethod
    def _safe_digest(value: object) -> str:
        return _safe_id(value, "inventory_digest")

    def create(
        self,
        remote_name: str,
        project_identity: str,
        workspace_label: str,
        *,
        mode: str = "reusable",
        checkout_locator: str | None = None,
        deployment_receipt: str | None = None,
        registration: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create/register a workspace without sending a checkout path.

        When injected seams are available, deployment and registration happen
        entirely through those callbacks.  Without seams, a metadata-only
        path-free controller create is allowed only when no checkout locator was
        supplied.  This makes an accidental local-path leak impossible.
        """
        identity = self._project_identity(project_identity)
        label = _safe_label(workspace_label, "workspace_label")
        mode = _safe_label(mode, "workspace mode")
        if checkout_locator is not None and (
            not isinstance(checkout_locator, str) or not checkout_locator.strip()
        ):
            raise RemoteWorkspaceError("workspace_checkout_unavailable", "checkout_locator is invalid")
        if deployment_receipt is not None:
            deployment_receipt = self._receipt(deployment_receipt)
        if registration is not None and not isinstance(registration, Mapping):
            raise RemoteWorkspaceError("workspace_request_invalid", "registration must be an object")
        request = WorkspaceCreateRequest(
            remote_name=remote_name,
            project_identity=identity,
            workspace_label=label,
            mode=mode,
            checkout_locator=checkout_locator,
            registration=dict(registration or {}),
        )
        prepared: Mapping[str, Any] | None = None
        if self.deploy is not None:
            try:
                prepared = self.deploy(request)
            except Exception as exc:
                detail = _safe_detail(str(exc))
                raise RemoteWorkspaceError(
                    "workspace_checkout_unavailable", detail or "checkout deployment failed"
                ) from None
            if prepared is not None and not isinstance(prepared, Mapping):
                raise RemoteWorkspaceError("workspace_protocol_invalid", "deployment result must be an object")
            prepared_receipt = self._prepared_receipt(prepared)
            if deployment_receipt is not None and prepared_receipt is not None and deployment_receipt != prepared_receipt:
                raise RemoteWorkspaceError("workspace_receipt_invalid", "deployment receipts do not match")
            deployment_receipt = deployment_receipt or prepared_receipt
        if self.register is not None:
            try:
                result = self.register(request, prepared)
            except Exception as exc:
                detail = _safe_detail(str(exc))
                raise RemoteWorkspaceError(
                    "workspace_create_failed", detail or "workspace registration failed"
                ) from None
            if not isinstance(result, Mapping):
                raise RemoteWorkspaceError("workspace_protocol_invalid", "registration result must be an object")
            payload = dict(result)
            if "ok" not in payload:
                payload = {"ok": True, **payload}
            return _success(payload, "workspace create")
        if deployment_receipt is not None or checkout_locator is not None or self.deploy is not None:
            if deployment_receipt is None:
                raise RemoteWorkspaceError(
                    "workspace_registration_unavailable",
                    "checkout-backed create requires an injected registration seam or receipt",
                )
            # A receipt/token is an opaque controller capability. It is the only
            # checkout-related value permitted on the remote command line; the
            # local checkout locator is deliberately never serialized.
            return self._control(remote_name, [
                "workspace", "create", "--project-identity", identity,
                "--workspace-label", label, "--mode", mode,
                "--deployment-receipt", deployment_receipt,
            ])
        return self._control(remote_name, [
            "workspace", "create", "--project-identity", identity,
            "--workspace-label", label, "--mode", mode,
        ])

    @staticmethod
    def _receipt(value: object) -> str:
        """Validate an opaque deployment receipt/token, never a filesystem path."""
        if not isinstance(value, str) or not value.strip():
            raise RemoteWorkspaceError("workspace_receipt_invalid", "deployment receipt is invalid")
        if "/" in value or "\\" in value or any(char.isspace() for char in value):
            raise RemoteWorkspaceError("workspace_receipt_invalid", "deployment receipt must be path-free")
        return _safe_id(value, "deployment receipt")

    @classmethod
    def _prepared_receipt(cls, prepared: Mapping[str, Any] | None) -> str | None:
        if prepared is None:
            return None
        values = [
            prepared.get(key)
            for key in ("deployment_receipt", "deployment_token", "receipt")
            if prepared.get(key) is not None
        ]
        if not values:
            return None
        if len({str(value) for value in values}) != 1:
            raise RemoteWorkspaceError("workspace_receipt_invalid", "deployment receipts are ambiguous")
        return cls._receipt(values[0])


__all__ = ["RemoteWorkspaceError", "RemoteWorkspaceTransport", "WorkspaceCreateRequest"]
