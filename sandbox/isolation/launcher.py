"""Single fail-closed gateway for every managed project-code entry path."""

from __future__ import annotations

from sandbox.isolation.credentials import sanitize_execution_context


ENTRY_PATHS = frozenset({
    "web_php", "cron", "wordpress_cli", "wp_eval", "exec", "composer",
    "plugin_activation", "phpunit", "durable_job",
})


class IsolationLauncher:
    def __init__(self, *, verifier, bubblewrap, machine_exec):
        self.verifier = verifier
        self.bubblewrap = bubblewrap
        self.machine_exec = machine_exec

    def launch(self, policy, *, entry_path, command, environment=None,
               credential_refs=(), timeout=300):
        if entry_path not in ENTRY_PATHS:
            return {"ok": False, "state": "blocked", "mutated": False,
                    "reason": {"code": "unsupported_execution_path"}}
        verified = self.verifier.verify(policy)
        if not verified.get("ok"):
            return {**verified, "operation": entry_path,
                    "reason": {"code": "isolation_prerequisite_missing",
                               "details": verified.get("reason")}}
        context = sanitize_execution_context(environment or {}, credential_refs)
        argv = self.bubblewrap.argv(
            environment=context["environment"], command=tuple(command),
        )
        result = self.machine_exec(
            policy.machine_id, argv, context=context, timeout=timeout,
            expected_policy_digest=policy.digest,
        )
        return {"ok": result.returncode == 0, "state": "ready" if result.returncode == 0
                else "failed", "operation": entry_path, "mutated": False,
                "exit_code": result.returncode,
                "reason": {"code": "ready" if result.returncode == 0 else
                           "isolated_payload_failed"}}
