"""Compare desired isolation policy with effective kernel/runtime observations."""

from __future__ import annotations


MANDATORY_NAMESPACES = frozenset({"user", "mount", "pid", "ipc", "uts", "network"})


class IsolationVerifier:
    def __init__(self, *, observe): self.observe = observe

    def verify(self, policy):
        try: observed = dict(self.observe(policy.machine_id))
        except Exception as exc:
            return {"ok": False, "state": "blocked", "mutated": False,
                    "reason": {"code": "isolation_observation_failed",
                               "message": str(exc)[:300]}}
        failures = []
        if observed.get("policy_digest") != policy.digest: failures.append("policy_digest")
        namespaces = observed.get("private_namespaces", {})
        for name in MANDATORY_NAMESPACES:
            if namespaces.get(name) is not True: failures.append(f"namespace:{name}")
        if observed.get("no_new_privileges") is not True: failures.append("no_new_privileges")
        if observed.get("capabilities") not in ((), []): failures.append("capabilities")
        expected_profile = f"sandbox-native-{policy.machine_id}//guest"
        if observed.get("apparmor_profile") != expected_profile: failures.append("apparmor_profile")
        if observed.get("nested_userns") is not False: failures.append("nested_userns")
        if observed.get("ambient_capabilities") not in ((), []): failures.append("ambient_capabilities")
        if observed.get("dangerous_capabilities") not in ((), []): failures.append("dangerous_capabilities")
        allowed_devices = {"null", "zero", "full", "random", "urandom", "tty", "pts", "ptmx", "shm"}
        if not isinstance(observed.get("devices"), (list, tuple, set, frozenset)) or \
                set(observed["devices"]) - allowed_devices:
            failures.append("devices")
        if observed.get("seccomp") is not True: failures.append("seccomp")
        if observed.get("nft_default_drop") is not True: failures.append("nft_default_drop")
        if observed.get("default_route") is not False: failures.append("default_route")
        expected_guest = str(policy.network.get("guest_address", ""))
        if observed.get("guest_address") != expected_guest: failures.append("guest_address")
        for boundary in ("host", "sibling", "metadata", "public"):
            if observed.get("reachability", {}).get(boundary) is not False:
                failures.append(f"reachability:{boundary}")
        if observed.get("cgroup_limits") != dict(policy.resources): failures.append("cgroup_limits")
        expected_ro = {item["target"] for item in policy.read_only_mounts}
        expected_rw = {item["target"] for item in policy.writable_mounts}
        if set(observed.get("read_only_mounts", ())) != expected_ro: failures.append("read_only_mounts")
        if set(observed.get("writable_mounts", ())) != expected_rw: failures.append("writable_mounts")
        if observed.get("unexpected_host_mounts") not in ((), []):
            failures.append("unexpected_host_mounts")
        for field in ("leaked_fds", "leaked_environment", "control_sockets"):
            if observed.get(field) not in ((), []): failures.append("credential_or_control_leak")
        return {"ok": not failures, "state": "ready" if not failures else "blocked",
                "mutated": False, "machine_id": policy.machine_id,
                "policy_digest": policy.digest,
                "reason": {"code": "ready" if not failures else "isolation_drift",
                           "failed_gates": failures}}
