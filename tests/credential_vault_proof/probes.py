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

# Commas and percent signs appear in fixed `ps`/`stat` format
# strings. Shell metacharacters stay out: argv arrays never reach a shell,
# and keeping them out means a mistake cannot become one either.
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9@._:/=+%,-]{1,255}$")

# The only executables a probe may ever name.
ALLOWED_EXECUTABLES = (
    "/usr/bin/cat", "/usr/bin/getent", "/usr/bin/ip", "/usr/bin/journalctl",
    "/usr/bin/ss", "/usr/bin/stat", "/usr/bin/systemctl", "/usr/bin/test",
    "/usr/sbin/nft", "/usr/bin/uname", "/usr/bin/ps", "/usr/bin/aa-status",
    "/usr/bin/lsb_release", "/usr/bin/id",
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


def _cgroup_path(manifest: dict[str, Any]) -> str:
    return "/sys/fs/cgroup" + manifest["service"]["cgroup"]


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


def _controller_unit_identity(manifest):
    return _argv("/usr/bin/systemctl", "show", manifest["service"]["controller_unit"],
                 "--property=Id", "--property=LoadState", "--property=ActiveState",
                 "--property=User", "--property=Group", "--property=ExecStart",
                 "--property=NoNewPrivileges", "--property=ControlGroup")


def _unit_ownership(manifest):
    return _argv("/usr/bin/systemctl", "show", _unit(manifest),
                 "--property=UID", "--property=GID", "--property=MainPID")


def _process_identity(manifest):
    return _argv("/usr/bin/ps", "-o", "pid=,uid=,lstart=,args=", "-C",
                 "native-credenti")


def _controller_identity(manifest):
    return _argv("/usr/bin/ps", "-o", "pid=,uid=,lstart=,args=", "-C",
                 "sandbox-credent")


def _executable_identity(manifest):
    return _argv("/usr/bin/stat", "-c", "%u:%g:%a:%s", manifest["service"]["executable"])


def _controller_executable_identity(manifest):
    return _argv("/usr/bin/stat", "-c", "%u:%g:%a:%s",
                 manifest["service"]["controller_executable"])


def _cgroup_identity(manifest):
    return _argv("/usr/bin/systemctl", "show", _unit(manifest),
                 "--property=ControlGroup")


def _lease_socket_owner(manifest):
    return _argv("/usr/bin/ss", "-x", "-p", "-e", "-H",
                 "src", _socket_name(manifest["transport"]["lease_socket"]))


def _controller_socket_owner(manifest):
    return _argv("/usr/bin/ss", "-x", "-p", "-e", "-H",
                 "src", _socket_name(manifest["transport"]["controller_socket"]))


def _guest_listener(manifest):
    transport = manifest["transport"]
    return _argv("/usr/bin/ss", "-tlnp", "-e", "-H",
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
    return _argv("/usr/bin/systemctl", "show", _unit(manifest),
                 "--property=BindPaths", "--property=BindReadOnlyPaths",
                 "--property=InaccessiblePaths", "--property=ProtectHome")


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
    return _argv("/usr/bin/stat", "-c", "%F", _cgroup_path(manifest))


def _process_absent(manifest):
    return _argv("/usr/bin/ps", "-o", "pid=", "-C", "native-credenti")


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
    return _argv("/usr/bin/stat", "-c", "%F", paths[0])


EXPECTATION_KINDS = ("exit_zero", "exit_nonzero", "empty_output")

_HOST_BUILDERS = {
    "os_release_supported": _os_release,
    "kernel_release_expected": _kernel_release,
    "architecture_expected": _architecture,
    "sandbox_revision_expected": _sandbox_revision,
    "unit_identity_expected": _unit_identity,
    "controller_unit_identity_expected": _controller_unit_identity,
    "unit_ownership_expected": _unit_ownership,
    "service_account_expected": _service_account,
    "broker_process_identity": _process_identity,
    "controller_process_identity": _controller_identity,
    "executable_ownership_expected": _executable_identity,
    "controller_executable_ownership_expected": _controller_executable_identity,
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


def _matched(kind: str, expected: Any, condition: bool) -> dict[str, Any]:
    return {"kind": kind, "value": expected if condition else None}


def _lines(stdout: str) -> list[str]:
    return [line.strip() for line in stdout.splitlines() if line.strip()]


def _fields(stdout: str) -> dict[str, str] | None:
    lines = _lines(stdout)
    if any("=" not in line for line in lines):
        return None
    pairs = [line.split("=", 1) for line in lines]
    if len({key for key, _ in pairs}) != len(pairs):
        return None
    return dict(pairs)


def _process_observation(check_id: str, stdout: str,
                         manifest: dict[str, Any]) -> dict[str, Any]:
    service = manifest["service"]
    uid = service["service_uid"] if check_id == "broker_process_identity" \
        else service["controller_uid"]
    executable = service["executable"] if check_id == "broker_process_identity" \
        else service["controller_executable"]
    expected = {"uid": uid, "executable": executable, "pid_positive": True,
                "start_time_typed": True}
    suffix = r"\s+(\S+)(?:\s+.*)?$"
    pattern = (r"^(\d+)\s+(\d+)\s+\S+\s+\S+\s+\d{1,2}\s+"
               r"\d{2}:\d{2}:\d{2}\s+\d{4}" + suffix)
    lines = _lines(stdout)
    match = re.fullmatch(pattern, lines[0]) if len(lines) == 1 else None
    correct = bool(match and int(match.group(1)) > 1 and int(match.group(2)) == uid)
    if correct:
        correct = match.group(3) == executable
    return {"kind": "process_identity",
            "value": {**expected, "pid": int(match.group(1))} if correct else None}


def _socket_observation(check_id: str, stdout: str,
                        manifest: dict[str, Any]) -> dict[str, Any]:
    transport = manifest["transport"]
    if check_id == "lease_socket_owned":
        address, owner = transport["lease_socket"], "native-credenti"
    elif check_id == "controller_socket_owned":
        address, owner = transport["controller_socket"], "sandbox-credent"
    else:
        address = f"{transport['host_address']}:{transport['guest_port']}"
        owner = "native-credenti"
    uid = (manifest["service"]["controller_uid"]
           if check_id == "controller_socket_owned"
           else manifest["service"]["service_uid"])
    expected = {"address": address, "owner": owner, "uid": uid,
                "pid_positive": True, "fd_nonnegative": True}
    lines = _lines(stdout)
    if len(lines) != 1 or address not in lines[0].split():
        return _matched("socket_owner", expected, False)
    owners = re.findall(r'users:\(\(\"([^\"]+)\",pid=(\d+),fd=(\d+)\)\)', lines[0])
    uids = re.findall(r"(?:^|\s)uid:(\d+)(?:\s|$)", lines[0])
    correct = len(owners) == 1 and owners[0][0] == owner \
        and int(owners[0][1]) > 1 and int(owners[0][2]) >= 0 \
        and len(uids) == 1 and int(uids[0]) == uid
    return {"kind": "socket_owner", "value": {
        **expected, "pid": int(owners[0][1]), "fd": int(owners[0][2]),
    } if correct else None}


def _json_contains_pair(value: Any, key: str, expected: Any) -> bool:
    if isinstance(value, dict):
        return value.get(key) == expected or any(
            _json_contains_pair(item, key, expected) for item in value.values())
    if isinstance(value, list):
        return any(_json_contains_pair(item, key, expected) for item in value)
    return False


def _nftables_matches(value: Any, table_name: str, policy: str) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("nftables"), list):
        return False
    objects = value["nftables"]
    table_ok = any(isinstance(item, dict) and isinstance(item.get("table"), dict)
                   and item["table"].get("family") == "inet"
                   and item["table"].get("name") == table_name for item in objects)
    chain_ok = any(isinstance(item, dict) and isinstance(item.get("chain"), dict)
                   and item["chain"].get("family") == "inet"
                   and item["chain"].get("table") == table_name
                   and item["chain"].get("policy") == policy for item in objects)
    return table_ok and chain_ok


def _expected_value(check_id: str, manifest: dict[str, Any]) -> Any:
    exact = _expected_text(check_id, manifest)
    if exact is not None:
        return exact
    service, transport, kernel = (manifest["service"], manifest["transport"],
                                  manifest["kernel"])
    values = {
        "unit_identity_expected": {
            "lines": sorted({f"Id={_unit(manifest)}", "LoadState=loaded",
                             "ActiveState=active", "User=sandbox-credential-broker",
                             "Group=sandbox-credential-broker", "NoNewPrivileges=yes",
                             f"ControlGroup={service['cgroup']}"}),
            "executable": service["executable"],
        },
        "controller_unit_identity_expected": {
            "lines": sorted({f"Id={service['controller_unit']}", "LoadState=loaded",
                             "ActiveState=active", "User=sandbox-credential-controller",
                             "Group=sandbox-credential-controller",
                             "NoNewPrivileges=yes",
                             f"ControlGroup={service['controller_cgroup']}"}),
            "executable": service["controller_executable"],
        },
        "unit_ownership_expected": {
            "UID": str(service["service_uid"]), "GID": str(service["service_gid"]),
            "MainPID": "positive",
        },
        "broker_process_identity": {
            "uid": service["service_uid"], "executable": service["executable"],
            "pid_positive": True, "start_time_typed": True,
        },
        "controller_process_identity": {
            "uid": service["controller_uid"],
            "executable": service["controller_executable"],
            "pid_positive": True, "start_time_typed": True,
        },
        "executable_ownership_expected": {
            "uid": service["service_uid"], "gid": service["service_gid"],
            "owner_executable": True, "group_world_not_writable": True,
            "size_positive": True,
        },
        "controller_executable_ownership_expected": {
            "uid": service["controller_uid"], "gid": service["controller_gid"],
            "owner_executable": True, "group_world_not_writable": True,
            "size_positive": True,
        },
        "cgroup_identity_expected": {"ControlGroup": service["cgroup"]},
        "lease_socket_owned": {
            "address": transport["lease_socket"], "owner": "native-credenti",
            "uid": service["service_uid"], "pid_positive": True,
            "fd_nonnegative": True,
        },
        "controller_socket_owned": {
            "address": transport["controller_socket"],
            "owner": "sandbox-credent", "uid": service["controller_uid"],
            "pid_positive": True,
            "fd_nonnegative": True,
        },
        "guest_listener_bound": {
            "address": f"{transport['host_address']}:{transport['guest_port']}",
            "owner": "native-credenti", "uid": service["service_uid"],
            "pid_positive": True,
            "fd_nonnegative": True,
        },
        "veth_identity_expected": {"interface": transport["guest_interface"]},
        "veth_address_expected": {"interface": transport["guest_interface"],
                                  "address": transport["host_address"]},
        "route_table_expected": {"interface": transport["guest_interface"],
                                 "destination": transport["guest_address"]},
        "nftables_default_drop": {"family": "inet", "table": kernel["nftables_table"],
                                  "policy": kernel["nftables_policy"]},
        "apparmor_profile_enforced": {"profile": kernel["apparmor_profile"],
                                      "mode": kernel["apparmor_mode"]},
        "no_unexpected_host_mount": {"BindPaths": "", "BindReadOnlyPaths": "",
                                     "InaccessiblePaths": "/home /root",
                                     "ProtectHome": "yes"},
        "unit_absent_after_cleanup": "LoadState=not-found",
        "process_absent_after_cleanup": {"comm": "native-credenti"},
        "interface_absent_after_cleanup": {"interface": transport["guest_interface"]},
        "route_absent_after_cleanup": {"interface": transport["guest_interface"]},
        "nftables_absent_after_cleanup": {"family": "inet",
                                          "table": kernel["nftables_table"]},
        "cgroup_absent_after_cleanup": {"path": _cgroup_path(manifest)},
        "temporary_absent_after_cleanup": {"path": manifest["cleanup"]["paths"][0]},
    }
    if check_id in values:
        return values[check_id]
    if catalog_module.CHECKS[check_id].source != "host_command":
        return {"check_id": check_id, "observed": True}
    if expectation_kind(check_id) == "empty_output":
        return ""
    if expectation_kind(check_id) == "exit_nonzero":
        return None
    return None


def _missing_observation(check_id: str, stdout: str, stderr: str,
                         manifest: dict[str, Any]) -> dict[str, Any]:
    transport, service, kernel = (manifest["transport"], manifest["service"],
                                  manifest["kernel"])
    if check_id == "process_absent_after_cleanup":
        return _matched("process_absent", {"comm": "native-credenti"},
                        not stdout.strip() and not stderr)
    if check_id in {"cgroup_absent_after_cleanup", "temporary_absent_after_cleanup"}:
        path = (_cgroup_path(manifest) if check_id == "cgroup_absent_after_cleanup"
                else manifest["cleanup"]["paths"][0])
        expected_stderr = f"/usr/bin/stat: cannot statx '{path}': No such file or directory\n"
        return _matched("path_absent", {"path": path},
                        not stdout.strip() and stderr == expected_stderr)
    if check_id == "interface_absent_after_cleanup":
        interface = transport["guest_interface"]
        return _matched("interface_absent", {"interface": interface},
                        not stdout.strip()
                        and stderr == f'Device "{interface}" does not exist.\n')
    if check_id == "route_absent_after_cleanup":
        interface = transport["guest_interface"]
        return _matched("route_absent", {"interface": interface},
                        not stdout.strip()
                        and stderr == f'Cannot find device "{interface}"\n')
    if check_id == "nftables_absent_after_cleanup":
        table = kernel["nftables_table"]
        lines = stderr.splitlines()
        correct = (not stdout.strip() and len(lines) == 3
                   and lines[0] == "Error: Could not process rule: No such file or directory"
                   and lines[1] == f"list table inet {table}"
                   and bool(re.fullmatch(r"\s*\^+", lines[2])))
        return _matched("nftables_absent", {"family": "inet", "table": table},
                        correct)
    return {"kind": "predicate_unavailable", "value": None}


def _normalize(check_id: str, stdout: str, stderr: str,
               manifest: dict[str, Any]) -> dict[str, Any]:
    """Produce only catalog-derived, secret-free typed observations."""
    exact = _expected_text(check_id, manifest)
    if exact is not None:
        return _matched("exact_text", exact, stdout.strip() == exact)
    if check_id in {"unit_identity_expected", "controller_unit_identity_expected"}:
        controller = check_id == "controller_unit_identity_expected"
        account = ("sandbox-credential-controller" if controller
                   else "sandbox-credential-broker")
        unit = (manifest["service"]["controller_unit"] if controller
                else _unit(manifest))
        cgroup = (manifest["service"]["controller_cgroup"] if controller
                  else manifest["service"]["cgroup"])
        required_fields = {
            "Id": unit, "LoadState": "loaded", "ActiveState": "active",
            "User": account, "Group": account, "NoNewPrivileges": "yes",
            "ControlGroup": cgroup,
        }
        fields = _fields(stdout) or {}
        executable = (manifest["service"]["controller_executable"] if controller
                      else manifest["service"]["executable"])
        start = fields.get("ExecStart", "")
        exec_ok = start == executable or start.startswith(f"{{ path={executable} ;")
        expected = _expected_value(check_id, manifest)
        return _matched("unit_identity", expected,
                        set(fields) == set(required_fields) | {"ExecStart"}
                        and all(fields.get(key) == value
                                for key, value in required_fields.items()) and exec_ok)
    if check_id == "unit_ownership_expected":
        fields = _fields(stdout) or {}
        expected = {"UID": str(manifest["service"]["service_uid"]),
                    "GID": str(manifest["service"]["service_gid"]),
                    "MainPID": "positive"}
        correct = set(fields) == {"UID", "GID", "MainPID"} \
            and fields["UID"] == expected["UID"] and fields["GID"] == expected["GID"] \
            and fields["MainPID"].isdigit() and int(fields["MainPID"]) > 1
        return _matched("unit_ownership", expected, correct)
    if check_id in {"broker_process_identity", "controller_process_identity"}:
        return _process_observation(check_id, stdout, manifest)
    if check_id in {"executable_ownership_expected",
                    "controller_executable_ownership_expected"}:
        expected = _expected_value(check_id, manifest)
        match = re.fullmatch(r"(\d+):(\d+):(\d{3,4}):(\d+)", stdout.strip())
        mode = int(match.group(3), 8) if match else 0
        correct = bool(match and int(match.group(1)) == expected["uid"]
                       and int(match.group(2)) == expected["gid"] and mode & 0o100
                       and not mode & 0o022 and int(match.group(4)) > 0)
        return _matched("executable_ownership", expected, correct)
    if check_id == "cgroup_identity_expected":
        expected = {"ControlGroup": manifest["service"]["cgroup"]}
        return _matched("systemd_fields", expected, _fields(stdout) == expected)
    if check_id in {"lease_socket_owned", "controller_socket_owned",
                    "guest_listener_bound"}:
        return _socket_observation(check_id, stdout, manifest)
    if check_id == "veth_identity_expected":
        expected = {"interface": manifest["transport"]["guest_interface"]}
        match = re.fullmatch(r"\d+:\s+([^:@]+)(?:@[^:]+)?:.*", stdout.strip())
        return _matched("link_identity", expected,
                        bool(match and match.group(1) == expected["interface"]))
    if check_id == "veth_address_expected":
        expected = {"interface": manifest["transport"]["guest_interface"],
                    "address": manifest["transport"]["host_address"]}
        match = re.fullmatch(r"\d+:\s+(\S+)\s+inet\s+([0-9.]+)/(\d+)(?:\s+.*)?",
                             stdout.strip())
        return _matched("interface_address", expected, bool(match and
                        match.group(1) == expected["interface"] and
                        match.group(2) == expected["address"] and
                        0 <= int(match.group(3)) <= 32))
    if check_id == "route_table_expected":
        expected = {"interface": manifest["transport"]["guest_interface"],
                    "destination": manifest["transport"]["guest_address"]}
        tokens = stdout.strip().split()
        correct = len(_lines(stdout)) == 1 and bool(tokens) \
            and tokens[0] == expected["destination"] and "dev" in tokens \
            and tokens.index("dev") + 1 < len(tokens) \
            and tokens[tokens.index("dev") + 1] == expected["interface"]
        return _matched("route", expected, correct)
    if check_id in {"nftables_default_drop", "apparmor_profile_enforced"}:
        try:
            value = json.loads(stdout)
        except json.JSONDecodeError:
            value = None
        if check_id == "nftables_default_drop":
            expected = {"family": "inet", "table": manifest["kernel"]["nftables_table"],
                        "policy": manifest["kernel"]["nftables_policy"]}
            correct = _nftables_matches(value, expected["table"], expected["policy"])
            return _matched("nftables_policy", expected, correct)
        expected = {"profile": manifest["kernel"]["apparmor_profile"],
                    "mode": manifest["kernel"]["apparmor_mode"]}
        return _matched("apparmor_profile", expected,
                        _json_contains_pair(value, expected["profile"], expected["mode"]))
    if check_id == "no_unexpected_host_mount":
        expected = {"BindPaths": "", "BindReadOnlyPaths": "",
                    "InaccessiblePaths": "/home /root", "ProtectHome": "yes"}
        return _matched("mount_isolation", expected, _fields(stdout) == expected)
    if catalog_module.CHECKS[check_id].source != "host_command":
        try:
            value = json.loads(stdout)
        except json.JSONDecodeError:
            value = None
        expected = {"check_id": check_id, "observed": True}
        return _matched("typed_source", expected, value == expected)
    if check_id == "unit_absent_after_cleanup":
        return _matched("unit_absent", "LoadState=not-found",
                        stdout.strip() == "LoadState=not-found")
    if expectation_kind(check_id) == "empty_output":
        return _matched("empty_output", "", not stdout.strip())
    if expectation_kind(check_id) == "exit_nonzero":
        return _missing_observation(check_id, stdout, stderr, manifest)
    return {"kind": "predicate_unavailable", "value": None}


def _observation_kind(check_id: str, manifest: dict[str, Any]) -> str:
    del manifest
    return catalog_module.CHECKS[check_id].predicate


def _observation_matches(check_id: str, observation: dict[str, Any],
                         manifest: dict[str, Any]) -> bool:
    kind, value = observation["kind"], observation["value"]
    if kind == "predicate_unavailable":
        return False
    if kind == "not_found_exit":
        return value is None
    if kind in {"process_identity", "socket_owner"}:
        if not isinstance(value, dict):
            return False
        dynamic = dict(value)
        pid = dynamic.pop("pid", None)
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
            return False
        if kind == "socket_owner":
            fd = dynamic.pop("fd", None)
            if not isinstance(fd, int) or isinstance(fd, bool) or fd < 0:
                return False
        return dynamic == _expected_value(check_id, manifest)
    return value is not None and value == _expected_value(check_id, manifest)


def _outcome(check_id: str, result: dict[str, Any], observation: dict[str, Any],
             manifest: dict[str, Any]
             ) -> tuple[str, str]:
    if result["timed_out"]:
        return "blocked", "probe_timeout"
    if observation.get("kind") == "secret_output":
        return "blocked", "secret_like_output"
    rc = result["returncode"]
    expectation = expectation_kind(check_id)
    if expectation == "exit_nonzero":
        if rc == 1 and _observation_matches(check_id, observation, manifest):
            return "passed", "observed_absent"
        if not result["stderr_empty"]:
            return "blocked", "probe_stderr"
        if rc in {126, 127} or rc < 0 or rc > 1:
            return "blocked", "probe_unavailable"
        return "failed", "resource_still_present"
    if not result["stderr_empty"]:
        return "blocked", "probe_stderr"
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
    socket_process = {
        "lease_socket_owned": "broker_process_identity",
        "controller_socket_owned": "controller_process_identity",
        "guest_listener_bound": "broker_process_identity",
    }
    for socket_check, process_check in socket_process.items():
        if socket_check not in observed or observed[socket_check]["state"] != "passed":
            continue
        socket_value = observed[socket_check]["observation"]["value"]
        process = observed.get(process_check, {})
        process_value = process.get("observation", {}).get("value")
        if not isinstance(socket_value, dict) or not isinstance(process_value, dict) \
                or process.get("state") != "passed" \
                or socket_value.get("pid") != process_value.get("pid") \
                or socket_value.get("uid") != process_value.get("uid"):
            raise _refuse("execution_artifact_socket_process_mismatch", "checks.json")
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
                   _normalize(check_id, stdout, stderr, manifest))
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
