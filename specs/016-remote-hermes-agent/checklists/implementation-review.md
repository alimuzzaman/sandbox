# Hermes implementation review (preliminary)

**Review type:** author self-review; this is not the independent review
required by T075.

**Scope reviewed:** the uncommitted implementation of the V1/V2 local control
plane in `sandbox/core/_hermes.py`, CLI presentation/parsing, MCP wrappers,
and operator documentation. V3 dashboard implementation is intentionally out
of scope while the V2 acceptance gate is pending.

## Evidence reviewed

- Targeted contract/core/MCP tests:
  `/Users/alim/Sites/git/sandbox/.cli-venv/bin/python -m unittest tests.test_mcp tests.test_hermes tests.test_cli -q`
  — 73 tests passed.
- Full unit suite:
  `/Users/alim/Sites/git/sandbox/.cli-venv/bin/python -m unittest discover -s tests -q`
  — 380 tests passed, one skipped.
- Static checks:
  `git diff --check` and
  `/Users/alim/Sites/git/sandbox/.cli-venv/bin/python -m py_compile sandbox/core/_hermes.py sandbox/commands/hermes.py mcp/wp-server/tools/hermes.py`
  — passed.
- Read-only supported-remote probes:
  `./sb hermes doctor --remote scaleway-sandbox --json`,
  `./sb hermes status --remote scaleway-sandbox --json`, and
  `./sb hermes acceptance v2 --remote scaleway-sandbox --json`.
  Doctor/status passed; the V2 gate correctly remains pending.

## Reviewed controls and findings

| Area | Finding | Resolution / residual risk |
| --- | --- | --- |
| Secret handling | Output redaction covers common `token`, `password`, `secret`, and `Authorization: Bearer` assignment forms. CLI and MCP responses use the common result envelope. | No secret test value is printed by the reviewed tests. Credentials must still be supplied only through the remote operator-owned flows. |
| Remote command construction | Managed names, job IDs, URLs, release revisions, backup IDs, allowlists, and arguments placed in shell commands are validated or shell-quoted before SSH execution. | Remote paths originate from Sandbox's configured home and remain a trusted configuration boundary. |
| Repository isolation | Clone destinations are contained below the managed root; per-repository advisory locks, randomized branches, and worktree-first runs avoid primary checkout modification. | A dirty or active worktree is retained for manual recovery; cleanup is confirmed and conservative. |
| Detached jobs | Detached runs create a new session/process group with `setsid`; cancellation signals the stored process group and status output is bounded. | A remote crash can leave stale state, which `health` reports and `cleanup` does not delete automatically when ambiguous. |
| Update and recovery | Update plans require an immutable tag/full commit; apply requires confirmation, creates a verified backup, runs health, and attempts restore on failure. Backup restore checks a SHA-256 sidecar and archive readability before replacement. | The fault-injection/restore/reboot path has not been exercised on the remote and cannot be certified from unit tests. |
| Gateway and dashboard | Gateway requires a non-empty explicit allowlist, uses a user systemd service, and checks lingering for reboot recovery. Dashboard actions fail closed until revision-bound V2 evidence passes. | Full unfiltered MCP and direct Sandbox CLI access are deliberately a trusted single-operator boundary; manual approvals are guidance, not a technical authorization wall. |
| API simplicity | The public CLI/MCP use stable sanitized envelopes and remote Hermes job IDs rather than reusing incompatible local async-job identifiers. | There is no dashboard surface before V2, as required. |

## Required follow-up

1. T023: run the approved clean-install, catalog, direct-CLI, worktree, and
   disposable-instance smoke on the supported remote.
2. T057: run the separately approved destructive V2 fault-injection and reboot
   procedure, then record only actual, revision-specific evidence.
3. T075: obtain an independent security/data/API-simplicity review. This
   preliminary author review must not be used to mark T075 complete.

## Conclusion

No unaddressed local implementation defect was identified in this preliminary
review. The implementation is not release-ready: V1 live smoke, V2 live
recovery/reboot evidence, and independent review remain mandatory gates.
