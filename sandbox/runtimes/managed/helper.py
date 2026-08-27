"""Unprivileged transport for fixed managed-native helper contracts."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from collections.abc import Mapping

from sandbox.isolation.models import canonical_digest
from sandbox.runtimes.managed.services import (
    CREDENTIAL_BROKER_SERVICE_UID, CREDENTIAL_BROKER_STATUS_FIELDS,
    credential_broker_cgroup_identity, credential_broker_unit_identity,
)


def validate_extension_package_allowlist(package_plan, *, catalog=None):
    """Validate catalog-derived PHP extension rows before helper staging.

    The fixed native helper already validates the ordinary package transaction
    schema.  This additional control-plane check binds the extension metadata
    to the same immutable catalog and ensures a project cannot smuggle an APT
    package, repository, PECL artifact, or source-build instruction through an
    otherwise valid image row.  It is deliberately side-effect free and raises
    ``ValueError`` on any unsafe shape.
    """
    if catalog is None:
        from sandbox.php_extensions.catalog import DEFAULT_CATALOG
        catalog = DEFAULT_CATALOG
    rows = getattr(package_plan, "image_packages", None)
    if rows is None and isinstance(package_plan, Mapping):
        rows = package_plan.get("image_packages")
    if not isinstance(rows, (tuple, list)):
        raise ValueError("managed package plan image rows are invalid")
    seen = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("managed package plan image row is invalid")
        metadata = row.get("php_extensions")
        if metadata is None:
            continue
        if (not isinstance(metadata, (tuple, list))
                or row.get("extension_provenance") != "official-distribution"
                or row.get("extension_catalog") != catalog.digest):
            raise ValueError("managed PHP extension package provenance is invalid")
        package = row.get("name")
        if not isinstance(package, str) or not re.fullmatch(r"php[0-9]+\.[0-9]+-[a-z0-9-]+", package):
            raise ValueError("managed PHP extension package name is invalid")
        match = re.match(r"^php(?P<minor>[0-9]+\.[0-9]+)-", package)
        php_minor = match.group("minor") if match else ""
        for item in metadata:
            if not isinstance(item, Mapping):
                raise ValueError("managed PHP extension package evidence is invalid")
            allowed = {"name", "state", "version", "package", "package_version",
                       "catalog_digest", "source"}
            if set(item) != allowed:
                raise ValueError("managed PHP extension package evidence is invalid")
            name = item.get("name")
            if not isinstance(name, str) or name in seen:
                raise ValueError("managed PHP extension package identity is invalid")
            seen.add(name)
            if (item.get("state") != "enabled"
                    or item.get("package") != package
                    or item.get("package_version") != row.get("version")
                    or item.get("catalog_digest") != catalog.digest
                    or item.get("source") != "official-distribution"):
                raise ValueError("managed PHP extension package provenance is invalid")
            try:
                recipe = catalog.recipe(name)
            except Exception as exc:
                raise ValueError("managed PHP extension is not in the allowlist") from exc
            expected = (recipe.package_template.format(php_minor=php_minor)
                        if recipe.package_template is not None
                        else f"php{php_minor}-common")
            if expected != package:
                raise ValueError("managed PHP extension package mapping is invalid")
    return True


class ManagedExtensionAllowlist:
    """Small injectable seam for callers that stage package plans."""

    def __init__(self, *, catalog=None):
        self.catalog = catalog

    def validate(self, package_plan):
        return validate_extension_package_allowlist(package_plan, catalog=self.catalog)


class ManagedMachineExecutor:
    """Stage project argv out of process listings and invoke the fixed helper."""

    def __init__(self, *, process, helper, staging_root="/var/lib/sandbox/native/staging"):
        self.process = process
        self.helper = helper
        self.staging_root = Path(staging_root)

    def __call__(self, machine_id, argv, *, context, timeout, expected_policy_digest):
        request = {
            "machine_id": machine_id, "policy_digest": expected_policy_digest,
            "argv": list(argv), "environment": dict(context.get("environment", {})),
            "credential_refs": list(context.get("credential_refs", ())),
            "timeout": timeout,
        }
        request_digest = canonical_digest(request)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        path = self.staging_root / f"execute-{os.getuid()}-{request_digest}.json"
        payload = (json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n").encode()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload); output.flush(); os.fsync(output.fileno())
            return self.process.run(("sudo", "-n", self.helper, "execute", machine_id,
                                     expected_policy_digest, request_digest),
                                    timeout=timeout + 15)
        finally:
            path.unlink(missing_ok=True)


class ManagedIsolationObserver:
    """Read one bounded effective-state document from the installed helper."""

    def __init__(self, *, process, helper):
        self.process = process
        self.helper = helper

    def __call__(self, machine_id):
        result = self.process.run(("sudo", "-n", self.helper, "isolation-observe",
                                   machine_id), timeout=20)
        if result.returncode != 0 or len((result.stdout or "").encode()) > 1024 * 1024:
            detail = ((result.stderr or "").strip() or "no output")
            raise RuntimeError(
                "managed isolation observation failed: "
                + (detail if len(detail) <= 400 else "…" + detail[-399:]))
        try: value = json.loads(result.stdout or "")
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("managed isolation observation is invalid") from exc
        if not isinstance(value, dict) or value.get("machine_id") != machine_id:
            raise RuntimeError("managed isolation observation is invalid")
        return value


class ManagedCleanupObserver:
    """Request one exact, read-only ownership proof before each removal."""

    RESOURCES = frozenset({
        "credential_broker", "services", "database", "machine", "network", "mount", "image", "policy",
    })

    def __init__(self, *, process, helper, credential_status=None):
        self.process = process
        self.helper = helper
        self.credential_status = credential_status

    def __call__(self, resource, plan):
        if resource not in self.RESOURCES or not isinstance(plan, dict):
            raise ValueError("managed cleanup observation is invalid")
        machine_id = plan.get("machine_id")
        policy_digest = plan.get("policy_digest")
        resource_digest = plan.get("digest") if resource in {"credential_broker", "services"} else policy_digest
        if resource == "credential_broker":
            if not callable(self.credential_status):
                raise RuntimeError("managed credential broker observation is unavailable")
            status = self.credential_status(plan)
            if not isinstance(status, dict) or status.get("state") == "drifted":
                raise RuntimeError("managed credential broker observation is invalid")
            if status.get("ok") is not True or status.get("state") == "unavailable":
                raise RuntimeError("managed credential broker observation failed")
            required = CREDENTIAL_BROKER_STATUS_FIELDS | {"mutated"}
            if (set(status) != required or not isinstance(status.get("admission_open"), bool)
                    or not isinstance(status.get("broker_epoch"), str)
                    or not re.fullmatch(r"[A-Za-z0-9/][A-Za-z0-9._:@/-]{0,255}",
                                        status["broker_epoch"])
                    or status.get("unit_identity") != credential_broker_unit_identity(machine_id)
                    or status.get("cgroup_identity") != credential_broker_cgroup_identity(machine_id)
                    or isinstance(status.get("pid"), bool) or not isinstance(status.get("pid"), int)
                    or status["pid"] < 1 or isinstance(status.get("service_uid"), bool)
                    or status.get("service_uid") != CREDENTIAL_BROKER_SERVICE_UID
                    or not isinstance(status.get("process_start_identity"), str)
                    or not status["process_start_identity"].startswith(f"{status['pid']}" + ":")):
                raise RuntimeError("managed credential broker observation is invalid")
            for name in ("machine_id", "policy_digest", "egress_digest", "broker_digest",
                         "executable_digest", "config_digest"):
                if status.get(name) != plan.get(name):
                    raise RuntimeError("managed credential broker observation is invalid")
            if status.get("state") == "stopped":
                if status["admission_open"] is not False:
                    raise RuntimeError("managed credential broker observation is invalid")
                state = "absent"
            elif (status.get("state") in {"credential_pending", "ready", "draining",
                                           "closed", "blocked"}
                  and isinstance(status.get("pid"), int)):
                state = "present"
            else:
                raise RuntimeError("managed credential broker observation is invalid")
            return {"machine_id": machine_id, "policy_digest": policy_digest,
                    "resource": resource, "resource_digest": resource_digest,
                    "state": state}
        result = self.process.run(
            ("sudo", "-n", self.helper, "cleanup-observe", resource,
             machine_id, policy_digest, resource_digest), timeout=30,
        )
        if result.returncode != 0 or len((result.stdout or "").encode()) > 65536:
            raise RuntimeError("managed cleanup observation failed")
        try:
            value = json.loads(result.stdout or "")
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("managed cleanup observation is invalid") from exc
        expected = {"machine_id": machine_id, "policy_digest": policy_digest,
                    "resource": resource, "resource_digest": resource_digest}
        if not isinstance(value, dict):
            raise RuntimeError("managed cleanup observation is invalid")
        # `state` reports whether the host still has the resource. Anything other
        # than the two known verdicts is a malformed observation, not a hint to
        # be interpreted: cleanup must never guess what an unknown state means.
        state = value.get("state", "present")
        if state not in {"present", "absent"}:
            raise RuntimeError("managed cleanup observation is invalid")
        if {key: item for key, item in value.items() if key != "state"} != expected:
            raise RuntimeError("managed cleanup observation is invalid")
        return {**expected, "state": state}
