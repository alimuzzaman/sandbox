"""Compare desired isolation policy with effective kernel/runtime observations."""

from __future__ import annotations

from sandbox.isolation.resources import memory_high_bytes


MANDATORY_NAMESPACES = frozenset({"user", "mount", "pid", "ipc", "uts", "network"})


class IsolationVerifier:
    def __init__(self, *, observe): self.observe = observe

    def verify(self, policy, *, grants=None):
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
        # The payload profile is entered by stacking it onto the bwrap profile at
        # the final exec, not by a domain transition, so the effective label is
        # the intersection of both and names both (FR-047). A bare `//payload`
        # would mean the transition happened, which NoNewPrivileges forbids.
        base = f"sandbox-native-{policy.machine_id}"
        expected_profile = f"{base}//bwrap//&{base}//payload"
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
        values = getattr(grants, "grants", ()) if grants is not None else ()
        active_grants = tuple(grant for grant in values
                              if not getattr(grant, "revoked", False))
        if active_grants:
            broker = observed.get("egress_broker", {})
            expected_ids = {grant.grant_id for grant in active_grants}
            if (not isinstance(broker, dict) or broker.get("ok") is not True or
                    broker.get("policy_digest") != policy.digest or
                    broker.get("grant_digest") != getattr(grants, "digest", None) or
                    broker.get("listener") != {
                        "address": str(policy.network.get("host_address", "")).split("/")[0],
                        "port": 18443, "interface": policy.network.get("veth"),
                    } or set(broker.get("grants", ())) != expected_ids):
                failures.append("egress_broker")
        for boundary in ("host", "sibling", "metadata", "public"):
            if observed.get("reachability", {}).get(boundary) is not False:
                failures.append(f"reachability:{boundary}")
        expected_resources = dict(policy.resources)
        if "memory_bytes" in expected_resources:
            expected_resources.update({
                "memory_high_bytes": memory_high_bytes(expected_resources["memory_bytes"]),
                "memory_swap_bytes": 0,
            })
        if observed.get("cgroup_limits") != expected_resources: failures.append("cgroup_limits")
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
