"""Single fail-closed gateway for every managed project-code entry path."""

from __future__ import annotations

from sandbox.isolation.credentials import sanitize_execution_context


ENTRY_PATHS = frozenset({
    "web_php", "cron", "wordpress_cli", "wp_eval", "exec", "composer",
    "plugin_activation", "phpunit", "durable_job",
})

# These paths live inside the per-instance image; they are not host bind mounts.
INSTANCE_WRITABLE_TARGETS = frozenset({
    "/var/www/html", "/var/lib/sandbox", "/var/log/sandbox", "/run/mysqld",
})


class IsolationLauncher:
    def __init__(self, *, verifier, bubblewrap, machine_exec):
        self.verifier = verifier
        self.bubblewrap = bubblewrap
        self.machine_exec = machine_exec

    def launch(self, policy, *, entry_path, command, environment=None,
               credential_refs=(), timeout=300, grants=None):
        if entry_path not in ENTRY_PATHS:
            return {"ok": False, "state": "blocked", "mutated": False,
                    "reason": {"code": "unsupported_execution_path"}}
        active_grants = ()
        if grants is not None:
            values = getattr(grants, "grants", grants)
            if not isinstance(values, (tuple, list)):
                return {"ok": False, "state": "blocked", "mutated": False,
                        "reason": {"code": "invalid_egress_grant_state"}}
            active_grants = tuple(grant for grant in values
                                  if not getattr(grant, "revoked", False))
        # An active egress capability changes the effective boundary.  Verify
        # its digest-bound broker immediately before every payload, not merely
        # when the grant was reconciled or status was last queried (FR-011,
        # FR-016).  Keep the no-grants call shape stable for baseline adapters.
        verified = (self.verifier.verify(policy, grants=grants) if active_grants
                    else self.verifier.verify(policy))
        if not verified.get("ok"):
            return {**verified, "operation": entry_path,
                    "reason": {"code": "isolation_prerequisite_missing",
                               "details": verified.get("reason")}}
        context = sanitize_execution_context(environment or {}, credential_refs)
        if active_grants:
            host = str(policy.network["host_address"]).split("/", 1)[0]
            proxy = f"http://{host}:18443"
            context["environment"].update({"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy})
        argv = self.bubblewrap.argv(
            environment=context["environment"],
            writable_targets=tuple({
                *(item["target"] for item in policy.writable_mounts),
                *INSTANCE_WRITABLE_TARGETS,
            }),
            credential_names=tuple(ref.rsplit("/", 1)[-1]
                                   for ref in context["credential_refs"]),
            command=tuple(command),
            # The payload profile is entered by stacking at the final exec; the
            # helper refuses an argv that does not carry the wrapper (FR-047).
            payload_profile=f"sandbox-native-{policy.machine_id}//payload",
        )
        result = self.machine_exec(
            policy.machine_id, argv, context=context, timeout=timeout,
            expected_policy_digest=policy.digest,
        )
        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""
        if not isinstance(stdout, str): stdout = str(stdout)
        if not isinstance(stderr, str): stderr = str(stderr)
        # Project output is user-visible transport data, never an authority
        # channel. Bound it so a hostile payload cannot exhaust the caller.
        stdout = stdout[:1024 * 1024]
        stderr = stderr[:1024 * 1024]
        return {"ok": result.returncode == 0, "state": "ready" if result.returncode == 0
                else "failed", "operation": entry_path, "mutated": False,
                "exit_code": result.returncode,
                "stdout": stdout, "stderr": stderr,
                "reason": {"code": "ready" if result.returncode == 0 else
                           "isolated_payload_failed"}}
