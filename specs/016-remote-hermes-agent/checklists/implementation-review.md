# Hermes implementation review

**Review type:** independent read-only security/data/API-simplicity review,
following the preliminary author review below. T075 passed on 2026-07-11 with
no actionable P1/P2 findings.

## Independent review resolution

The reviewer verified the final recovery and security controls in
`sandbox/core/_hermes.py` and `tests/test_hermes.py`: signed installer
provenance; recursive public-result redaction; revision/schema-bound V2
gating; loopback dashboard checks; exact source/virtualenv/launcher backup and
restore; automatic and public missing-runtime recovery; active-gateway
resumption; and nested forbidden source-path rejection. New archives contain
the launcher; compatible restore regenerates it from the restored virtualenv
for earlier archives. Post-restore setup reapplies only the Sandbox MCP/profile
integration and does not restore provider credentials.

Evidence: focused Hermes tests (83), full unit suite (413 passed, one existing
skip), `git diff --check`, Python compilation, and disposable-remote verified
backup/restore plus healthy doctor output. The reviewer found no new secret
output or public exposure path.

---

## Preliminary author review

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

1. T023: complete the clean-account provider-authenticated one-shot prompt and
   on-demand disposable-instance smoke. Clean install/setup/doctor and recovery
   evidence now exists on the resettable `hermes-acceptance` remote, but no
   provider credential was copied into that account.

## Conclusion

T075 is complete. The remaining release gate is T023's deliberately isolated,
operator-authenticated V1 one-shot and on-demand-instance smoke.
