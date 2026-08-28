"""Command builders and parsers for the future Ubuntu 24.04 live checks.

Every probe is an argv array built from a fixed allowlist and the manifest's own
derived identifiers. A caller cannot pass a command, a path, a unit, an
interface, or an environment name: it can only name a check id the catalog
already knows.

Nothing in this module executes anything. It builds plans and parses bounded
output that a future authorized run will hand back.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from . import catalog as catalog_module
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


def _process_absent(manifest):
    return _argv("/usr/bin/ps", "-o", "pid=", "-C", "native-credential-broker")


def _route_absent(manifest):
    return _argv("/usr/bin/ip", "-o", "route", "show", "dev",
                 manifest["transport"]["guest_interface"])


def _nftables_absent(manifest):
    return _argv("/usr/sbin/nft", "list", "table", "inet",
                 manifest["kernel"]["nftables_table"])


def _temporary_absent(manifest):
    paths = manifest["cleanup"]["paths"]
    if not paths:
        raise _refuse("cleanup_path_unknown")
    return _argv("/usr/bin/test", "-e", paths[0])


EXPECTATION_KINDS = ("exit_zero", "exit_nonzero", "empty_output")

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
    "process_absent_after_cleanup": _process_absent,
    "route_absent_after_cleanup": _route_absent,
    "nftables_absent_after_cleanup": _nftables_absent,
    "socket_absent_after_cleanup": _socket_absent,
    "interface_absent_after_cleanup": _interface_absent,
    "cgroup_absent_after_cleanup": _cgroup_absent,
    "temporary_absent_after_cleanup": _temporary_absent,
}

CHECK_IDS = tuple(catalog_module.CHECKS)


def catalog() -> tuple[str, ...]:
    return CHECK_IDS


def expectation_kind(check_id: Any) -> str:
    """What a passing result looks like for this check."""
    if not isinstance(check_id, str) or check_id not in CHECK_IDS:
        raise _refuse("check_unknown", str(check_id)[:64])
    return catalog_module.CHECKS[check_id].expectation


def build(check_id: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    """Return one bounded execution plan for a known check id."""
    if not isinstance(check_id, str) or check_id not in CHECK_IDS:
        raise _refuse("check_unknown", str(check_id)[:64])
    if not isinstance(manifest, dict):
        raise _refuse("manifest_invalid")
    timeout = manifest["bounds"]["command_timeout_seconds"]
    output_bytes = min(manifest["bounds"]["max_output_bytes"], MAX_OUTPUT_BYTES)
    expectation = expectation_kind(check_id)
    definition = catalog_module.CHECKS[check_id]
    if definition.source != "host_command":
        return {
            "check_id": check_id,
            "category": definition.category,
            "kind": definition.source,
            "argv": (),
            "expectation": expectation,
            "timeout_seconds": timeout,
            "max_output_bytes": output_bytes,
            "redact": True,
        }
    builder = _HOST_BUILDERS.get(check_id)
    if builder is None:
        raise _refuse("catalog_builder_missing", check_id)
    return {
        "check_id": check_id,
        "category": definition.category,
        "kind": "host_command",
        "argv": builder(manifest),
        "expectation": expectation,
        "timeout_seconds": timeout,
        "max_output_bytes": output_bytes,
        "redact": True,
    }


def plan(manifest: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Build one plan entry per check the manifest asks for, in manifest order."""
    return tuple(build(item["check_id"], manifest) for item in manifest["checks"])


_EXECUTION_FIELDS = frozenset({
    "check_id", "category", "source", "expectation", "argv", "result",
    "observation",
})
_RESULT_FIELDS = frozenset({
    "returncode", "timed_out", "stderr_empty", "raw_result_digest",
})


def _expected_text(check_id: str, manifest: dict[str, Any]) -> str | None:
    values = {
        "os_release_supported": manifest["platform"]["os_release"].removeprefix("ubuntu-"),
        "kernel_release_expected": manifest["platform"]["kernel_release"],
        "architecture_expected": manifest["platform"]["architecture"],
        "sandbox_revision_expected": manifest["source"]["sandbox_revision"],
        "service_account_expected": str(manifest["service"]["service_uid"]),
    }
    return values.get(check_id)


def _contains_expectations(check_id: str, manifest: dict[str, Any]
                           ) -> tuple[str, ...] | None:
    return {
        "broker_process_identity": (str(manifest["service"]["service_uid"]),
                                    manifest["service"]["executable"]),
        "controller_process_identity": (str(manifest["service"]["controller_uid"]),),
        "cgroup_identity_expected": (f"ControlGroup={manifest['service']['cgroup']}",),
        "lease_socket_owned": (manifest["transport"]["lease_socket"],),
        "controller_socket_owned": (manifest["transport"]["controller_socket"],),
        "guest_listener_bound": (manifest["transport"]["host_address"],
                                 str(manifest["transport"]["guest_port"])),
        "veth_identity_expected": (manifest["transport"]["guest_interface"],),
        "veth_address_expected": (manifest["transport"]["guest_interface"],
                                  manifest["transport"]["guest_address"]),
        "route_table_expected": (manifest["transport"]["guest_interface"],),
        "nftables_default_drop": (manifest["kernel"]["nftables_table"],
                                  manifest["kernel"]["nftables_policy"]),
        "apparmor_profile_enforced": (manifest["kernel"]["apparmor_profile"],
                                      manifest["kernel"]["apparmor_mode"]),
    }.get(check_id)


def _normalize(check_id: str, stdout: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """Produce only catalog-derived, secret-free typed observations."""
    exact = _expected_text(check_id, manifest)
    if exact is not None:
        return {"kind": "exact_text", "value": exact if stdout.strip() == exact else None}
    if check_id == "unit_identity_expected":
        required = {
            f"Id={_unit(manifest)}", "LoadState=loaded", "ActiveState=active",
            "User=sandbox-credential-broker", "Group=sandbox-credential-broker",
            "NoNewPrivileges=yes", f"ControlGroup={manifest['service']['cgroup']}",
        }
        lines = set(stdout.splitlines())
        executable = manifest["service"]["executable"]
        return {"kind": "unit_identity", "value": {
            "lines": sorted(required & lines),
            "exec_start": (f"ExecStart={executable}" if any(
                line.startswith("ExecStart=") and executable in line for line in lines)
                else None),
        }}
    if check_id == "unit_ownership_expected":
        fields = dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)
        matched = fields.get("UID") == str(manifest["service"]["service_uid"]) \
            and fields.get("GID") == str(manifest["service"]["service_gid"]) \
            and fields.get("MainPID", "").isdigit() and int(fields["MainPID"]) > 1
        return {"kind": "unit_ownership", "value": {
            key: fields[key] for key in ("UID", "GID", "MainPID") if key in fields
        } if matched else {}}
    contains = _contains_expectations(check_id, manifest)
    if contains is not None:
        return {"kind": "contains_all", "value": [item for item in contains
                                                     if item in stdout]}
    if catalog_module.CHECKS[check_id].source != "host_command":
        try:
            value = json.loads(stdout)
        except json.JSONDecodeError:
            value = None
        expected = {"check_id": check_id, "observed": True}
        return {"kind": "typed_source", "value": expected if value == expected else None}
    if check_id == "unit_absent_after_cleanup":
        value = "LoadState=not-found"
        return {"kind": "unit_absent", "value": value if stdout.strip() == value else None}
    if expectation_kind(check_id) == "empty_output":
        return {"kind": "empty_output", "value": "" if not stdout.strip() else None}
    if expectation_kind(check_id) == "exit_nonzero":
        return {"kind": "not_found_exit", "value": None}
    # A catalogued host check without a typed predicate cannot contribute a
    # pass. This is safer than treating arbitrary non-empty output as proof.
    return {"kind": "predicate_unavailable", "value": None}


def _observation_kind(check_id: str, manifest: dict[str, Any]) -> str:
    return _normalize(check_id, "", manifest)["kind"]


def _observation_matches(check_id: str, observation: dict[str, Any],
                         manifest: dict[str, Any]) -> bool:
    kind, value = observation["kind"], observation["value"]
    exact = _expected_text(check_id, manifest)
    if kind == "exact_text":
        return value == exact
    if kind == "unit_identity":
        required = sorted({
            f"Id={_unit(manifest)}", "LoadState=loaded", "ActiveState=active",
            "User=sandbox-credential-broker", "Group=sandbox-credential-broker",
            "NoNewPrivileges=yes", f"ControlGroup={manifest['service']['cgroup']}",
        })
        return isinstance(value, dict) and value.get("lines") == required \
            and isinstance(value.get("exec_start"), str) \
            and manifest["service"]["executable"] in value["exec_start"]
    if kind == "unit_ownership":
        return isinstance(value, dict) \
            and value.get("UID") == str(manifest["service"]["service_uid"]) \
            and value.get("GID") == str(manifest["service"]["service_gid"]) \
            and str(value.get("MainPID", "")).isdigit() \
            and int(value["MainPID"]) > 1
    if kind == "contains_all":
        expected = _contains_expectations(check_id, manifest)
        return isinstance(value, list) and value == list(expected or ())
    if kind == "typed_source":
        return value == {"check_id": check_id, "observed": True}
    if kind == "unit_absent":
        return value == "LoadState=not-found"
    if kind == "empty_output":
        return value == ""
    if kind == "not_found_exit":
        return value is None
    return False


def _outcome(check_id: str, result: dict[str, Any], observation: dict[str, Any],
             manifest: dict[str, Any]
             ) -> tuple[str, str]:
    if result["timed_out"]:
        return "blocked", "probe_timeout"
    if observation.get("kind") == "secret_output":
        return "blocked", "secret_like_output"
    if not result["stderr_empty"]:
        return "blocked", "probe_stderr"
    rc = result["returncode"]
    expectation = expectation_kind(check_id)
    if expectation == "exit_nonzero":
        if rc == 1 and _observation_matches(check_id, observation, manifest):
            return "passed", "observed_absent"
        if rc in {126, 127} or rc < 0 or rc > 1:
            return "blocked", "probe_unavailable"
        return "failed", "resource_still_present"
    if expectation == "empty_output" and rc != 0:
        return "blocked", "probe_unavailable"
    if rc in {126, 127} or rc < 0:
        return "blocked", "probe_unavailable"
    if rc != 0:
        return "failed", "probe_nonzero_exit"
    if observation.get("kind") == "predicate_unavailable":
        return "blocked", "typed_predicate_unavailable"
    if not _observation_matches(check_id, observation, manifest):
        code = "resource_still_present" if expectation == "empty_output" \
            or check_id == "unit_absent_after_cleanup" else "observation_mismatch"
        return "failed", code
    code = "observed_absent" if expectation != "exit_zero" \
        or check_id == "unit_absent_after_cleanup" else "observed"
    return "passed", code


def validate_execution_artifact(document: Any, manifest: dict[str, Any]
                                ) -> tuple[dict[str, Any], ...]:
    """Bind retained check results to the exact catalog-derived execution plan."""
    planned = {entry["check_id"]: entry for entry in plan(manifest)}
    if not isinstance(document, list) or len(document) != len(planned):
        raise _refuse("execution_artifact_incomplete", "checks.json")
    observed: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(document):
        location = f"checks.json[{index}]"
        if not isinstance(item, dict) or frozenset(item) != _EXECUTION_FIELDS:
            raise _refuse("execution_artifact_schema_invalid", location)
        check_id = item["check_id"]
        expected = planned.get(check_id)
        if expected is None or check_id in observed:
            raise _refuse("execution_artifact_check_invalid", location)
        if item["category"] != expected["category"]:
            raise _refuse("execution_artifact_category_mismatch", location)
        if item["source"] != expected["kind"]:
            raise _refuse("execution_artifact_source_mismatch", location)
        if item["expectation"] != expected["expectation"]:
            raise _refuse("execution_artifact_expectation_mismatch", location)
        if not isinstance(item["argv"], list) \
                or tuple(item["argv"]) != expected["argv"]:
            raise _refuse("execution_artifact_argv_mismatch", location)
        result = item["result"]
        if not isinstance(result, dict) or frozenset(result) != _RESULT_FIELDS \
                or isinstance(result["returncode"], bool) \
                or not isinstance(result["returncode"], int) \
                or not isinstance(result["timed_out"], bool) \
                or not isinstance(result["stderr_empty"], bool) \
                or not isinstance(result["raw_result_digest"], str) \
                or not re.fullmatch(r"[0-9a-f]{64}", result["raw_result_digest"]):
            raise _refuse("execution_artifact_result_invalid", location)
        observation = item["observation"]
        if not isinstance(observation, dict) or frozenset(observation) != {
                "kind", "value"} or not isinstance(observation["kind"], str):
            raise _refuse("execution_artifact_observation_invalid", location)
        try:
            encoded_observation = json.dumps(observation, sort_keys=True,
                                             separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise _refuse("execution_artifact_observation_invalid", location) from exc
        if len(encoded_observation.encode()) > 4096 \
                or scanner.scan_document(observation, location=location):
            raise _refuse("execution_artifact_observation_invalid", location)
        if observation["kind"] != _observation_kind(check_id, manifest):
            raise _refuse("execution_artifact_observation_kind_mismatch", location)
        # Recompute from typed metadata. State/code are intentionally absent.
        state, code = _outcome(check_id, result, observation, manifest)
        observed[check_id] = {**item, "state": state, "code": code}
    if set(observed) != set(planned):
        raise _refuse("execution_artifact_incomplete", "checks.json")
    return tuple(observed[name] for name in planned)


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
                                                  "timed_out"}):
        raise _refuse("result_schema_invalid", check_id)
    limit = min(manifest["bounds"]["max_output_bytes"], MAX_OUTPUT_BYTES)
    stdout = _bounded_output(completed["stdout"], limit)
    stderr = _bounded_output(completed["stderr"], limit)
    if not isinstance(completed["timed_out"], bool):
        raise _refuse("result_schema_invalid", check_id)
    if not isinstance(completed["returncode"], int) \
            or isinstance(completed["returncode"], bool):
        raise _refuse("result_schema_invalid", check_id)
    findings = scanner.scan_text(stdout, location=f"{check_id}.stdout")
    observation = ({"kind": "secret_output", "value": None} if findings else
                   _normalize(check_id, stdout, manifest))
    raw = {"returncode": completed["returncode"], "stdout": stdout,
           "stderr": stderr, "timed_out": completed["timed_out"]}
    result = {
        "returncode": completed["returncode"], "timed_out": completed["timed_out"],
        "stderr_empty": not stderr, "raw_result_digest": hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }
    state, code = _outcome(check_id, result, observation, manifest)
    return {"check_id": check_id, "state": state, "code": code,
            "observation": observation, "result": result,
            "findings": tuple(item["code"] for item in findings)}


__all__ = [
    "ALLOWED_EXECUTABLES", "CHECK_IDS", "EXPECTATION_KINDS", "MAX_ARGV",
    "MAX_OUTPUT_BYTES", "ProbeError", "build", "catalog", "expectation_kind",
    "parse", "plan", "validate_execution_artifact",
]
