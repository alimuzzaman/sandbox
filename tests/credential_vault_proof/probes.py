"""Command builders and parsers for the future Ubuntu 24.04 live checks.

Every probe is an argv array built from a fixed allowlist and the manifest's own
derived identifiers. A caller cannot pass a command, a path, a unit, an
interface, or an environment name: it can only name a check id the catalog
already knows.

Nothing in this module executes anything. It builds plans and parses bounded
output that a future authorized run will hand back.
"""

from __future__ import annotations

import re
from typing import Any

from . import scanner


MAX_ARGV = 24
MAX_OUTPUT_BYTES = 64 * 1024
MAX_OBSERVATIONS = 32

# Commas and percent signs appear in fixed `ps`/`stat`/`findmnt` format
# strings. Shell metacharacters stay out: argv arrays never reach a shell,
# and keeping them out means a mistake cannot become one either.
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9@._:/=+%,-]{1,255}$")

# The only executables a probe may ever name.
ALLOWED_EXECUTABLES = (
    "/usr/bin/cat", "/usr/bin/getent", "/usr/bin/ip", "/usr/bin/journalctl",
    "/usr/bin/ss", "/usr/bin/stat", "/usr/bin/systemctl", "/usr/bin/test",
    "/usr/sbin/nft", "/usr/bin/uname", "/usr/bin/ps", "/usr/bin/aa-status",
    "/usr/bin/lsb_release", "/usr/bin/findmnt", "/usr/bin/id",
)


class ProbeError(ValueError):
    def __init__(self, code: str, location: str = "probe") -> None:
        super().__init__(code)
        self.code = code
        self.location = location[:256]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "location": self.location}


def _refuse(code: str, location: str = "probe") -> ProbeError:
    return ProbeError(code, location)


def _argv(*parts: Any) -> tuple[str, ...]:
    values = []
    for part in parts:
        if not isinstance(part, str) or not _SAFE_TOKEN.fullmatch(part):
            raise _refuse("argv_token_invalid")
        values.append(part)
    if not values or values[0] not in ALLOWED_EXECUTABLES:
        raise _refuse("executable_not_allowed")
    if len(values) > MAX_ARGV:
        raise _refuse("argv_too_long")
    return tuple(values)


def _unit(manifest: dict[str, Any], index: int = 0) -> str:
    units = manifest["service"]["units"]
    if index >= len(units):
        raise _refuse("unit_unknown")
    return units[index]


def _socket_name(value: str) -> str:
    # `ss` reports an abstract socket without its leading NUL; the manifest
    # spells it with '@', which is the same convention.
    return value


# --- builders ---------------------------------------------------------------
# Each builder takes the validated manifest and returns one argv array. They are
# deliberately small and boring: the safety comes from the allowlist above and
# from every identifier being manifest-derived.

def _os_release(manifest):
    return _argv("/usr/bin/lsb_release", "-sr")


def _kernel_release(manifest):
    return _argv("/usr/bin/uname", "-r")


def _architecture(manifest):
    return _argv("/usr/bin/uname", "-m")


def _sandbox_revision(manifest):
    return _argv("/usr/bin/cat", "/etc/sandbox/native/revision")


def _unit_identity(manifest):
    return _argv("/usr/bin/systemctl", "show", _unit(manifest),
                 "--property=Id", "--property=LoadState", "--property=ActiveState",
                 "--property=User", "--property=Group", "--property=ExecStart",
                 "--property=NoNewPrivileges", "--property=ControlGroup")


def _unit_ownership(manifest):
    return _argv("/usr/bin/systemctl", "show", _unit(manifest),
                 "--property=UID", "--property=GID", "--property=MainPID")


def _process_identity(manifest):
    return _argv("/usr/bin/ps", "-o", "pid=,uid=,lstart=,args=", "-C",
                 "native-credential-broker")


def _controller_identity(manifest):
    return _argv("/usr/bin/ps", "-o", "pid=,uid=,lstart=", "-C",
                 "sandbox-credential-controller")


def _executable_identity(manifest):
    return _argv("/usr/bin/stat", "-c", "%u:%g:%a:%s", manifest["service"]["executable"])


def _cgroup_identity(manifest):
    return _argv("/usr/bin/systemctl", "show", _unit(manifest),
                 "--property=ControlGroup")


def _lease_socket_owner(manifest):
    return _argv("/usr/bin/ss", "-x", "-p", "-H",
                 "src", _socket_name(manifest["transport"]["lease_socket"]))


def _controller_socket_owner(manifest):
    return _argv("/usr/bin/ss", "-x", "-p", "-H",
                 "src", _socket_name(manifest["transport"]["controller_socket"]))


def _guest_listener(manifest):
    transport = manifest["transport"]
    return _argv("/usr/bin/ss", "-tlnp", "-H",
                 "src", f"{transport['host_address']}:{transport['guest_port']}")


def _interface_identity(manifest):
    return _argv("/usr/bin/ip", "-o", "link", "show",
                 manifest["transport"]["guest_interface"])


def _interface_address(manifest):
    return _argv("/usr/bin/ip", "-o", "-4", "addr", "show",
                 manifest["transport"]["guest_interface"])


def _route_table(manifest):
    return _argv("/usr/bin/ip", "-o", "route", "show", "dev",
                 manifest["transport"]["guest_interface"])


def _nftables_state(manifest):
    return _argv("/usr/sbin/nft", "-j", "list", "table", "inet",
                 manifest["kernel"]["nftables_table"])


def _apparmor_state(manifest):
    return _argv("/usr/bin/aa-status", "--json")


def _mount_state(manifest):
    return _argv("/usr/bin/findmnt", "-J", "-o", "TARGET,SOURCE,OPTIONS")


def _service_account(manifest):
    return _argv("/usr/bin/id", "-u", "sandbox-credential-broker")


def _unit_absent(manifest):
    return _argv("/usr/bin/systemctl", "show", _unit(manifest),
                 "--property=LoadState")


def _socket_absent(manifest):
    return _argv("/usr/bin/ss", "-x", "-H", "src",
                 _socket_name(manifest["transport"]["lease_socket"]))


def _interface_absent(manifest):
    return _argv("/usr/bin/ip", "-o", "link", "show",
                 manifest["transport"]["guest_interface"])


def _cgroup_absent(manifest):
    return _argv("/usr/bin/test", "-d", manifest["service"]["cgroup"])


def _temporary_absent(manifest):
    paths = manifest["cleanup"]["paths"]
    if not paths:
        raise _refuse("cleanup_path_unknown")
    return _argv("/usr/bin/test", "-e", paths[0])


# Checks whose evidence comes from the broker's own bounded status or from a
# guest-side probe the future run drives, not from a host command. They are
# still catalogued so the manifest can require them and the ledger can record
# them; their plan says explicitly where the evidence must come from.
_GUEST_SOURCED = {
    "peer_credentials_observed": "broker_status",
    "scm_credentials_observed": "broker_status",
    "scm_rights_exactly_one": "broker_status",
    "memfd_type_and_seals": "broker_status",
    "descriptor_closed_on_success": "broker_status",
    "descriptor_closed_on_failure": "broker_status",
    "guest_cannot_reach_controller": "guest_probe",
    "guest_cannot_reach_lease_socket": "guest_probe",
    "guest_cannot_reach_host": "guest_probe",
    "guest_cannot_reach_loopback": "guest_probe",
    "guest_cannot_reach_metadata": "guest_probe",
    "guest_cannot_reach_other_interface": "guest_probe",
    "bindtodevice_enforced": "guest_probe",
    "dns_pinning_enforced": "guest_probe",
    "tls_verification_enforced": "guest_probe",
    "redirect_refused": "guest_probe",
    "response_size_bounded": "guest_probe",
    "request_timeout_bounded": "guest_probe",
    "concurrency_ceiling_enforced": "guest_probe",
    "epoch_rotates_on_restart": "broker_status",
    "quiesce_before_drain": "broker_status",
    "drain_precedes_stop": "broker_status",
    "descriptor_absent_after_cleanup": "broker_status",
}

_HOST_BUILDERS = {
    "os_release_supported": _os_release,
    "kernel_release_expected": _kernel_release,
    "architecture_expected": _architecture,
    "sandbox_revision_expected": _sandbox_revision,
    "unit_identity_expected": _unit_identity,
    "unit_ownership_expected": _unit_ownership,
    "service_account_expected": _service_account,
    "broker_process_identity": _process_identity,
    "controller_process_identity": _controller_identity,
    "executable_ownership_expected": _executable_identity,
    "cgroup_identity_expected": _cgroup_identity,
    "lease_socket_owned": _lease_socket_owner,
    "controller_socket_owned": _controller_socket_owner,
    "guest_listener_bound": _guest_listener,
    "veth_identity_expected": _interface_identity,
    "veth_address_expected": _interface_address,
    "route_table_expected": _route_table,
    "nftables_default_drop": _nftables_state,
    "apparmor_profile_enforced": _apparmor_state,
    "no_unexpected_host_mount": _mount_state,
    "unit_absent_after_cleanup": _unit_absent,
    "socket_absent_after_cleanup": _socket_absent,
    "interface_absent_after_cleanup": _interface_absent,
    "cgroup_absent_after_cleanup": _cgroup_absent,
    "temporary_absent_after_cleanup": _temporary_absent,
}

CHECK_IDS = tuple(sorted(set(_HOST_BUILDERS) | set(_GUEST_SOURCED)))


def catalog() -> tuple[str, ...]:
    return CHECK_IDS


def build(check_id: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    """Return one bounded execution plan for a known check id."""
    if not isinstance(check_id, str) or check_id not in CHECK_IDS:
        raise _refuse("check_unknown", str(check_id)[:64])
    if not isinstance(manifest, dict):
        raise _refuse("manifest_invalid")
    timeout = manifest["bounds"]["command_timeout_seconds"]
    output_bytes = min(manifest["bounds"]["max_output_bytes"], MAX_OUTPUT_BYTES)
    if check_id in _GUEST_SOURCED:
        return {
            "check_id": check_id,
            "kind": _GUEST_SOURCED[check_id],
            "argv": (),
            "timeout_seconds": timeout,
            "max_output_bytes": output_bytes,
            "redact": True,
        }
    return {
        "check_id": check_id,
        "kind": "host_command",
        "argv": _HOST_BUILDERS[check_id](manifest),
        "timeout_seconds": timeout,
        "max_output_bytes": output_bytes,
        "redact": True,
    }


def plan(manifest: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Build one plan entry per check the manifest asks for, in manifest order."""
    return tuple(build(item["check_id"], manifest) for item in manifest["checks"])


def _bounded_output(value: Any, limit: int) -> str:
    if isinstance(value, (bytes, bytearray)):
        try:
            value = bytes(value).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise _refuse("output_undecodable") from None
    if not isinstance(value, str):
        raise _refuse("output_invalid")
    if len(value.encode("utf-8")) > limit:
        raise _refuse("output_oversize")
    return value


def parse(check_id: Any, completed: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    """Turn one bounded result into a state plus redacted observations.

    Raw stdout and stderr are never carried forward. What survives is the exit
    status, a small set of matched observations, and whether the scanner found
    anything that must not be persisted.
    """
    if not isinstance(check_id, str) or check_id not in CHECK_IDS:
        raise _refuse("check_unknown", str(check_id)[:64])
    if not isinstance(completed, dict) \
            or frozenset(completed) != frozenset({"returncode", "stdout", "stderr",
                                                  "timed_out", "expected"}):
        raise _refuse("result_schema_invalid", check_id)
    limit = min(manifest["bounds"]["max_output_bytes"], MAX_OUTPUT_BYTES)
    stdout = _bounded_output(completed["stdout"], limit)
    _bounded_output(completed["stderr"], limit)
    if not isinstance(completed["timed_out"], bool):
        raise _refuse("result_schema_invalid", check_id)
    if not isinstance(completed["returncode"], int) \
            or isinstance(completed["returncode"], bool):
        raise _refuse("result_schema_invalid", check_id)
    expected = completed["expected"]
    if not isinstance(expected, list) or len(expected) > MAX_OBSERVATIONS \
            or any(not isinstance(item, str) or len(item) > 256 for item in expected):
        raise _refuse("result_schema_invalid", check_id)
    findings = scanner.scan_text(stdout, location=f"{check_id}.stdout")
    if findings:
        return {"check_id": check_id, "state": "blocked",
                "code": "secret_like_output", "observations": (),
                "findings": tuple(item["code"] for item in findings)}
    if completed["timed_out"]:
        return {"check_id": check_id, "state": "blocked", "code": "probe_timeout",
                "observations": (), "findings": ()}
    matched = tuple(item for item in expected if item in stdout)
    if completed["returncode"] != 0:
        return {"check_id": check_id, "state": "failed", "code": "probe_nonzero_exit",
                "observations": matched, "findings": ()}
    if len(matched) != len(expected):
        return {"check_id": check_id, "state": "failed",
                "code": "expected_observation_missing", "observations": matched,
                "findings": ()}
    return {"check_id": check_id, "state": "passed", "code": "observed",
            "observations": matched, "findings": ()}


__all__ = [
    "ALLOWED_EXECUTABLES", "CHECK_IDS", "MAX_ARGV", "MAX_OUTPUT_BYTES",
    "ProbeError", "build", "catalog", "parse", "plan",
]
