# Safe source `CODEX-SRC-b0cd2f139137896fc41b`

Source class: remote storage and durable resource workflow
Evidence role: primary detailed rollout plus Luna Max source-contract expansion

## Findings sourced here

### ATO-001 — Durable job observer (P1)

The rollout contained 118 `job-status` calls, 43 `job-output` calls, and 77
`sleep` commands. Luna Max anchored the pattern to eight durable job IDs; three
representative jobs received 38, 31, and 15 status calls while callers alternated
output reads and 30–55 second sleeps. Add a bounded CLI/MCP observer that returns
one terminal receipt. See [canonical finding](../findings.md#ato-001--add-a-durable-job-observer).

### ATO-003 — Machine-readable job contract (P1/P2)

Manual parsers failed on repeated JSON envelopes, retained JSONL, and unexpected
field shapes. Define one JSON/JSONL contract and expose structured result and
completeness fields.

### ATO-004 — Remote readiness receipt (P2)

Workspace/revision mismatch and migration recovery were rediscovered repeatedly.
Add a read-only capability/revision receipt, preserving explicit migration.

### ATO-005 — Bootstrap context (P2)

Exact duplicates included guide and skill loading. Add one cacheable context
receipt for project, target, revision, and safe next commands.

### ATO-007 — Feedback export guidance (P3)

The workflow used feedback listing and manual cursor/JSON handling even though
bounded export exists. Prefer `feedback export --format jsonl` in agent guidance.

### ATO-008 — Test selection ergonomics (P3)

Broad unittest runs were repeatedly narrowed or interrupted, with shell typos in
the surrounding commands. Add clearer named test selection and incomplete-run
summaries before adding larger runner machinery.

### ATO-009 — Aggregate agent waits (P3 / adjacent)

The rollout recorded 182 collaboration waits, 32 agent paths, and 9 interruptions.
Batching waits is an orchestration improvement outside Sandbox's core CLI.

## Luna Max source-contract findings attached to this transcript

These were discovered while expanding this workflow against the current source;
they are not claims that the original agent consciously observed each source bug.

- **ATO-011 (P1/P2):** resource calls use the installed `/resources` control
  service, while docs claim source-shipped probes and resource readiness lacks the
  workspace-style ownership/revision gate.
- **ATO-012 (P2):** remote provision/up can claim readiness after service
  installation without the existing authenticated `/mcp` doctor probe.
- **ATO-013 (P2):** non-zero SSH status probes can be wrapped as positive-looking
  observed JSON instead of typed transport-unavailable evidence.
- **ATO-014 (P3):** resource docs/spec drift around walk depth and network
  cleanup eligibility.
- **ATO-015 (P3):** Spec 036 wording conflates persisted diagnostic cache state
  with durable cleanup/mutation state.
- **ATO-016 (P2):** confirmed repeated provisioning mints a fresh token and can
  invalidate existing MCP clients instead of converging safely.
