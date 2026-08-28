"""Synthetic, non-secret fixtures for the harness's own offline tests.

Every value here is invented. There is no real host, no real revision, no real
credential, and nothing in this module is ever presented as evidence: fixture
runs carry the `local_injected_fake` provenance, which the bundle validator
refuses as live proof by design.
"""

from __future__ import annotations

from typing import Any


GIT_SHA = "0" * 39 + "1"
SANDBOX_REVISION = "sandbox-0.0.0-fixture"
MACHINE_ID = "sb-0123456789ab"
BROKER_EPOCH = "epoch-fixture-0001"
HOST_LABEL = "proof-host-fixture"

# A deliberately obvious non-secret marker for the no-leak tests. It is not a
# credential and never leaves this package's test fixtures.
SYNTHETIC_MARKER = "synthetic-credential-must-never-escape"

# Secret-SHAPED probe strings. None of these is a real credential; they exist so
# a test can assert that the harness refuses this shape. They live here, in the
# one dedicated fixture module, so no other file has to carry them.
SECRET_SHAPED = {
    "authorization_header": "authorization: Bearer aaaaaaaaaaaaaaaaaaaa",
    "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
    "internal_identifier": "operation_id",
}


def manifest(**overrides: Any) -> dict[str, Any]:
    document = {
        "version": 1,
        "manifest_id": "credential-vault-proof-fixture",
        "source": {"git_sha": GIT_SHA, "sandbox_revision": SANDBOX_REVISION},
        "target": {"machine_id": MACHINE_ID, "broker_epoch": BROKER_EPOCH,
                   "host_label": HOST_LABEL},
        "platform": {"os_release": "ubuntu-24.04", "kernel_release": "6.8.0-31-generic",
                     "architecture": "x86_64"},
        "service": {
            "units": ["sandbox-credential-broker@sb-0123456789ab.service"],
            "service_uid": 991, "service_gid": 991,
            "controller_uid": 501, "controller_gid": 501,
            "executable": "/usr/libexec/sandbox/native-credential-broker",
            "executable_digest": "d" * 64, "config_digest": "e" * 64,
            "cgroup": "/sandbox.slice/credential-broker/sb-0123456789ab",
        },
        "transport": {
            "guest_interface": "ve-sb01234567", "host_address": "10.203.0.1",
            "guest_address": "10.203.0.2", "guest_port": 18443,
            "lease_socket": "@sandbox-credential-broker-lease-0001",
            "controller_socket": "@sandbox-credential-broker-control-0001",
        },
        "kernel": {
            "required_capabilities": [],
            "forbidden_capabilities": ["CAP_SYS_ADMIN", "CAP_SYS_PTRACE",
                                       "CAP_NET_ADMIN"],
            "apparmor_profile": "sandbox-native-sb-0123456789ab",
            "apparmor_mode": "enforce", "seccomp_mode": "filter",
            "nftables_table": "sandbox-native-sb0123456789ab",
            "nftables_policy": "drop",
        },
        "bounds": {
            "connect_seconds": 5, "total_seconds": 30, "idle_seconds": 5,
            "drain_seconds": 5, "command_timeout_seconds": 30,
            "max_request_headers": 65536, "max_request_body": 1048576,
            "max_response_body": 4194304, "max_concurrent": 16,
            "max_output_bytes": 65536,
        },
        "cleanup": {
            "units": ["sandbox-credential-broker@sb-0123456789ab.service"],
            "sockets": ["@sandbox-credential-broker-lease-0001",
                        "@sandbox-credential-broker-control-0001"],
            "interfaces": ["ve-sb01234567"],
            "cgroups": ["/sandbox.slice/credential-broker/sb-0123456789ab"],
            "nftables_objects": ["sandbox-native-sb0123456789ab"],
            "paths": ["/run/sandbox-native/credential-broker"],
        },
        "checks": [
            {"check_id": "os_release_supported", "category": "platform",
             "required": True, "description": "host runs the supported release"},
            {"check_id": "sandbox_revision_expected", "category": "revision",
             "required": True, "description": "installed revision matches the plan"},
            {"check_id": "unit_identity_expected", "category": "service_identity",
             "required": True, "description": "unit identity and ownership match"},
            {"check_id": "lease_socket_owned", "category": "transport",
             "required": True, "description": "lease socket is broker owned"},
            {"check_id": "scm_rights_exactly_one", "category": "descriptor",
             "required": True, "description": "exactly one descriptor is accepted"},
            {"check_id": "guest_cannot_reach_controller", "category": "network",
             "required": True, "description": "guest cannot reach the controller"},
            {"check_id": "unit_absent_after_cleanup", "category": "cleanup",
             "required": True, "description": "unit is gone after cleanup"},
            {"check_id": "route_table_expected", "category": "network",
             "required": False, "description": "route table matches the plan"},
        ],
        "artifacts": [
            {"name": "checks.json", "sha256": None, "max_bytes": 262144},
            {"name": "cleanup.json", "sha256": None, "max_bytes": 65536},
        ],
    }
    document.update(overrides)
    return document


def acceptance(*, manifest_document: Any = None, request_id: str = "cv-proof-0001",
               accepted_at: str = "2026-09-01T10:01:00Z", **overrides: Any
               ) -> dict[str, Any]:
    document = manifest_document or manifest()
    from .manifest import manifest_digest

    value = {
        "accepted": True, "job_id": "job-fixture-0001",
        "request_id": request_id, "manifest_digest": manifest_digest(document),
        "machine_id": document["target"]["machine_id"],
        "broker_epoch": document["target"]["broker_epoch"],
        "git_sha": document["source"]["git_sha"],
        "sandbox_revision": document["source"]["sandbox_revision"],
        "accepted_at": accepted_at,
    }
    value.update(overrides)
    return value


def cleanup_observations(manifest_document: Any, *, state: str = "absent",
                         owned: bool = True) -> tuple[dict[str, Any], ...]:
    from .cleanup import expected_resources

    return tuple({
        "kind": item["kind"], "identity": item["identity"], "state": state,
        "owned": owned,
    } for item in expected_resources(manifest_document))


def events(check_states: Any, *, start_at: str = "2026-09-01T10:00:00Z"
           ) -> list[dict[str, Any]]:
    """Build a well-ordered event list for the given terminal check states."""
    document: list[dict[str, Any]] = []
    sequence = 0
    hour, minute = 10, 0
    for check_id, state in check_states.items():
        for phase in ("started", state):
            sequence += 1
            minute += 1
            if minute >= 60:
                minute = 0
                hour += 1
            document.append({
                "sequence": sequence,
                "at": f"2026-09-01T{hour:02d}:{minute:02d}:00Z",
                "check_id": check_id,
                "state": phase,
                "code": "observed" if phase != "started" else "started",
            })
    return document


def execution_artifact(manifest_document: Any, check_states: Any
                       ) -> list[dict[str, Any]]:
    from .probes import plan

    return [{
        "check_id": entry["check_id"], "category": entry["category"],
        "source": entry["kind"], "expectation": entry["expectation"],
        "argv": list(entry["argv"]), "state": check_states.get(entry["check_id"],
                                                               "passed"),
        "code": "observed",
    } for entry in plan(manifest_document)]


def cleanup_artifact(manifest_document: Any) -> dict[str, Any]:
    from .cleanup import verify

    observations = list(cleanup_observations(manifest_document))
    result = verify(manifest_document, observations)
    return {"observations": observations, "state": result["state"]}


__all__ = [
    "BROKER_EPOCH", "GIT_SHA", "HOST_LABEL", "MACHINE_ID", "SANDBOX_REVISION",
    "SYNTHETIC_MARKER", "acceptance", "cleanup_artifact", "cleanup_observations",
    "events", "execution_artifact", "manifest",
]
