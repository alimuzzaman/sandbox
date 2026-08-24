# Safe source `CODEX-SRC-9d3983e2ec663eac3b54`

Source class: Codex CLI and retention contract review
Evidence role: destructive default and confirmation-boundary cross-check

## Findings sourced here

### ATO-020 — Job-retention confirmation gate (P1/P2)

The transcript ran `job-retention --json` and recorded cleanup of many historical
job log/metric records without a dry-run or confirmation flag. A later bounded
query returned no candidates, showing the deletion persisted. Default retention
should preview; deletion should require explicit confirmation and a bounded
receipt. See [canonical finding](../findings.md#ato-020--put-a-confirmation-gate-on-job-retention-deletion).
