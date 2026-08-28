"""Shared closed runtime preparation for the credential v2 service roles.

This module performs only canonical no-follow config loading and exact process
identity pinning.  It installs no unit, opens no socket, reads no repository,
and grants no credential authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from types import MappingProxyType
from typing import Any, Mapping

from .credential_controller_lifecycle_v2 import (
    DerivedServiceConfigV2,
    validate_reciprocal_service_plans_v2,
)
from .credential_controller_service_v2 import (
    ControllerServiceConfig,
    ControllerServiceV2Error,
    ProcessIdentity,
)


MAX_CONFIG_BYTES_V2 = 16384
_MACHINE = re.compile(r"^[a-z0-9][a-z0-9-]{6,61}[a-z0-9]$")


def runtime_config_path_v2(machine_id: str, component: str) -> str:
    if (not isinstance(machine_id, str) or _MACHINE.fullmatch(machine_id) is None
            or component not in {"controller", "broker"}):
        raise ControllerServiceV2Error("runtime_config_invalid")
    return f"/etc/sandbox/credential-v2/{component}/{machine_id}.json"


class ConfigKernelV2:
    open = staticmethod(os.open)
    fstat = staticmethod(os.fstat)
    read = staticmethod(os.read)
    close = staticmethod(os.close)


def load_runtime_config_v2(path: str, *, machine_id: str, component: str,
                           expected_group_gid: int,
                           kernel=None) -> DerivedServiceConfigV2:
    """Load exactly one root-owned canonical config from its fixed path."""

    if (path != runtime_config_path_v2(machine_id, component)
            or type(expected_group_gid) is not int
            or not 1 <= expected_group_gid <= 2**31 - 1):
        raise ControllerServiceV2Error("runtime_config_invalid")
    selected = kernel or ConfigKernelV2()
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = selected.open(path, flags)
        observed = selected.fstat(descriptor)
        if (not stat.S_ISREG(observed.st_mode) or stat.S_ISLNK(observed.st_mode)
                or observed.st_uid != 0 or observed.st_gid != expected_group_gid
                or stat.S_IMODE(observed.st_mode) != 0o640
                or not 1 <= observed.st_size <= MAX_CONFIG_BYTES_V2):
            raise ValueError
        raw = bytearray()
        while len(raw) <= MAX_CONFIG_BYTES_V2:
            chunk = selected.read(
                descriptor, min(4096, MAX_CONFIG_BYTES_V2 + 1 - len(raw)))
            if not isinstance(chunk, bytes):
                raise ValueError
            if not chunk:
                break
            raw.extend(chunk)
        payload = bytes(raw)
        if len(payload) != observed.st_size:
            raise ValueError
        document = json.loads(payload.decode("ascii"))
        plan = DerivedServiceConfigV2.derive(document)
        if (plan.component != component or plan.machine_id != machine_id
                or plan.service_gid != expected_group_gid
                or plan.canonical_bytes != payload
                or plan.config_digest != hashlib.sha256(payload).hexdigest()):
            raise ValueError
        return plan
    except (ControllerServiceV2Error, OSError):
        raise
    except Exception:
        raise ControllerServiceV2Error("runtime_config_invalid") from None
    finally:
        if descriptor is not None:
            selected.close(descriptor)


def process_cgroup_identity_v2(plan: DerivedServiceConfigV2) -> str:
    if not isinstance(plan, DerivedServiceConfigV2):
        raise ControllerServiceV2Error("peer_identity_unavailable")
    return f"/system.slice/{plan.document['unit_identity']}"


def _linux_start_ticks_v2(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="ascii") as stream:
            value = stream.read(4096)
        end = value.rfind(")")
        fields = value[end + 2:].split() if end >= 0 else []
        selected = int(fields[19])
    except Exception:
        raise ControllerServiceV2Error("peer_identity_unavailable") from None
    if not 1 <= selected <= 2**63 - 1:
        raise ControllerServiceV2Error("peer_identity_unavailable")
    return selected


def _linux_cgroup_pid_v2(plan: DerivedServiceConfigV2) -> int:
    try:
        path = f"/sys/fs/cgroup{process_cgroup_identity_v2(plan)}/cgroup.procs"
        with open(path, "r", encoding="ascii") as stream:
            values = stream.read(128).splitlines()
        if len(values) != 1 or not values[0].isdigit():
            raise ValueError
        pid = int(values[0])
    except Exception:
        raise ControllerServiceV2Error("peer_identity_unavailable") from None
    if not 1 <= pid <= 2**31 - 1:
        raise ControllerServiceV2Error("peer_identity_unavailable")
    return pid


def _linux_executable_digest_v2(pid: int) -> str:
    try:
        digest = hashlib.sha256()
        with open(f"/proc/{pid}/exe", "rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        raise ControllerServiceV2Error("peer_identity_unavailable") from None


def _linux_process_details_v2(pid: int, plan: DerivedServiceConfigV2) -> Mapping[str, Any]:
    try:
        with open(f"/proc/{pid}/status", "r", encoding="ascii") as stream:
            status = stream.read(16384).splitlines()
        uids = next(line for line in status if line.startswith("Uid:")).split()[1:]
        gids = next(line for line in status if line.startswith("Gid:")).split()[1:]
        with open(f"/proc/{pid}/cgroup", "r", encoding="ascii") as stream:
            cgroup = stream.read(4096).splitlines()
        if (len(uids) != 4 or len(gids) != 4 or len(set(uids)) != 1
                or len(set(gids)) != 1
                or cgroup != [f"0::{process_cgroup_identity_v2(plan)}"]):
            raise ValueError
        return {
            "uid": int(uids[0]), "gid": int(gids[0]),
            "executable_digest": _linux_executable_digest_v2(pid),
            "unit_digest": plan.document["unit_digest"],
            "config_digest": plan.document["own_config_digest"],
        }
    except Exception:
        raise ControllerServiceV2Error("peer_identity_unavailable") from None


def pin_process_identity_v2(plan: DerivedServiceConfigV2, *,
                            cgroup_pid_reader=_linux_cgroup_pid_v2,
                            start_reader=_linux_start_ticks_v2,
                            detail_reader=_linux_process_details_v2) -> ProcessIdentity:
    """Pin exactly one process with start/observe/start PID-reuse resistance."""

    if (not isinstance(plan, DerivedServiceConfigV2)
            or plan.document["process_identity_authority"] != "sealed_systemd_cgroup_v2"
            or not all(callable(value) for value in (
                cgroup_pid_reader, start_reader, detail_reader))):
        raise ControllerServiceV2Error("peer_identity_unavailable")
    try:
        pid = cgroup_pid_reader(plan)
        first = start_reader(pid)
        details = detail_reader(pid, plan)
        second = start_reader(pid)
        identity = ProcessIdentity(
            uid=details["uid"], gid=details["gid"], pid=pid,
            start_ticks=first, executable_digest=details["executable_digest"],
            unit_digest=details["unit_digest"], config_digest=details["config_digest"])
    except ControllerServiceV2Error:
        raise
    except Exception:
        raise ControllerServiceV2Error("peer_identity_unavailable") from None
    if (first != second
            or identity.uid != plan.document["service_uid"]
            or identity.gid != plan.document["service_gid"]
            or identity.executable_digest != plan.document["executable_digest"]
            or identity.unit_digest != plan.document["unit_digest"]
            or identity.config_digest != plan.document["own_config_digest"]):
        raise ControllerServiceV2Error("peer_identity_mismatch")
    return identity


def pin_reciprocal_process_identities_v2(controller: DerivedServiceConfigV2,
                                         broker: DerivedServiceConfigV2, **readers):
    validate_reciprocal_service_plans_v2(controller, broker)
    return (pin_process_identity_v2(controller, **readers),
            pin_process_identity_v2(broker, **readers))


def pinned_process_identity_observer_v2(expected: ProcessIdentity):
    if type(expected) is not ProcessIdentity:
        raise ControllerServiceV2Error("identity_observer_invalid")

    def observe(pid: int, uid: int, gid: int) -> ProcessIdentity:
        if (pid, uid, gid) != (expected.pid, expected.uid, expected.gid):
            raise ControllerServiceV2Error("peer_identity_mismatch")
        return expected
    return observe


def prepare_reciprocal_service_runtime_v2(
        machine_id: str, *, service_gid: int,
        plan_loader=load_runtime_config_v2,
        identity_pinner=pin_reciprocal_process_identities_v2):
    """Load both plans before pinning either process; return immutable facts."""

    if (not isinstance(machine_id, str) or _MACHINE.fullmatch(machine_id) is None
            or type(service_gid) is not int or not 1 <= service_gid <= 2**31 - 1
            or not callable(plan_loader) or not callable(identity_pinner)):
        raise ControllerServiceV2Error("runtime_config_invalid")
    try:
        controller = plan_loader(
            runtime_config_path_v2(machine_id, "controller"), machine_id=machine_id,
            component="controller", expected_group_gid=service_gid)
        broker = plan_loader(
            runtime_config_path_v2(machine_id, "broker"), machine_id=machine_id,
            component="broker", expected_group_gid=service_gid)
        validate_reciprocal_service_plans_v2(controller, broker)
        controller_identity, broker_identity = identity_pinner(controller, broker)
        config = ControllerServiceConfig(
            machine_id=machine_id, controller=controller_identity,
            broker=broker_identity, policy_digest=broker.document["policy_digest"],
            egress_digest=broker.document["egress_digest"],
            broker_digest=broker.document["broker_digest"],
            proof_digest=broker.document["proof_digest"],
            effective_isolation_digest=broker.document["effective_isolation_digest"],
            evidence_id=broker.document["evidence_id"])
    except Exception:
        raise ControllerServiceV2Error("runtime_config_invalid") from None
    return MappingProxyType({
        "controller_plan": controller, "broker_plan": broker, "config": config,
        "controller_observer": pinned_process_identity_observer_v2(controller_identity),
        "broker_observer": pinned_process_identity_observer_v2(broker_identity),
    })


def prepare_controller_process_runtime_v2(
        machine_id: str, *, service_gid: int,
        plan_loader=load_runtime_config_v2,
        identity_pinner=pin_process_identity_v2):
    """Prepare the already-started controller without requiring a broker PID yet."""

    if (not isinstance(machine_id, str) or _MACHINE.fullmatch(machine_id) is None
            or type(service_gid) is not int or not 1 <= service_gid <= 2**31 - 1
            or not callable(plan_loader) or not callable(identity_pinner)):
        raise ControllerServiceV2Error("runtime_config_invalid")
    try:
        controller = plan_loader(
            runtime_config_path_v2(machine_id, "controller"), machine_id=machine_id,
            component="controller", expected_group_gid=service_gid)
        broker = plan_loader(
            runtime_config_path_v2(machine_id, "broker"), machine_id=machine_id,
            component="broker", expected_group_gid=service_gid)
        validate_reciprocal_service_plans_v2(controller, broker)
        controller_identity = identity_pinner(controller)
    except Exception:
        raise ControllerServiceV2Error("runtime_config_invalid") from None
    return MappingProxyType({
        "controller_plan": controller, "broker_plan": broker,
        "controller_identity": controller_identity,
        "controller_observer": pinned_process_identity_observer_v2(controller_identity),
        "broker_identity_pinner": identity_pinner,
    })


__all__ = [
    "ConfigKernelV2", "MAX_CONFIG_BYTES_V2", "load_runtime_config_v2",
    "pin_process_identity_v2", "pin_reciprocal_process_identities_v2",
    "pinned_process_identity_observer_v2", "prepare_reciprocal_service_runtime_v2",
    "prepare_controller_process_runtime_v2",
    "process_cgroup_identity_v2", "runtime_config_path_v2",
]
