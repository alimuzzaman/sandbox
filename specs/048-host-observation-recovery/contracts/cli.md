# CLI Contract: `sb host recover`

`sb host status --json` exposes `generation` and a bounded `latest_recovery` summary. This is
the supported discovery path for `--expected-generation`; callers never read managed state.

## Observation/reconciliation

```sh
./sb host recover \
  --project-dir /path/to/project \
  --environment development \
  --remote NAME \
  --job-id JOB_ID \
  --original-request-id APPLY_REQUEST \
  --request-id RECOVERY_REQUEST \
  --expected-generation N \
  --json
```

This is receipt-only and accepts no `--confirm`. It exits 0 only for a success-family result.
Refusal, uncertainty, and failure exit non-zero after printing one complete JSON object.
Both `--project-dir` and `--environment` must be supplied explicitly. Omission refuses before
manifest inference, target construction, remote lookup, or state/broker writers. With `--json`,
that refusal prints exactly one bounded schema-1 `binding_mismatch` envelope and exits nonzero;
it never mixes a human error line into stdout.

## Edge continuation

```sh
./sb host recover ... \
  --request-id EDGE_REQUEST \
  --continue-edge \
  --observation-request-id OBSERVATION_REQUEST \
  --evidence-id EVIDENCE_ID \
  --expected-generation N \
  --confirm \
  --json
```

Edge request identity must differ from apply and observation identities. `--continue-edge`
without `--confirm` refuses before effects. Observation mode with `--confirm` is invalid.
Feature 047's immutable-image edge journal is operation-local replay authority, not a
host-recovery governance projection. This revision does not treat that journal or image
state as approval, so its public edge command always refuses with
`governance_unavailable`. The adapter contract
is retained as an inactive, tested seam for a later governed activation. A service instance
with no explicit governance verifier also fails closed and cannot call that adapter.

## JSON envelope

```json
{
  "ok": false,
  "schema_version": 1,
  "action": "observe_reconcile",
  "result_family": "refused",
  "result_class": "legacy_evidence",
  "request_id": "recover-...",
  "original": {"job_id": "...", "request_id": "..."},
  "target": {"remote": "...", "project": "...", "environment": "..."},
  "generation": {"expected": 3, "resulting": 3},
  "effect_scope": "receipt_only",
  "evidence": {"id": null, "complete": false},
  "phases": []
}
```

Stable result families: `success`, `refused`, `uncertain`, `failed`.

Required stable classes include `observation_reconciled`, `already_reconciled`,
`edge_only_completed`, `legacy_evidence`, `job_ineligible`, `binding_mismatch`,
`dirty_source`, `changed_target`, `partial_evidence`, `evidence_changed`,
`generation_conflict`, `operation_busy`, `mutation_required`, `governance_unavailable`,
`confirmation_required`, `expired_evidence`, `effect_unknown`, `observation_failed`,
`edge_failed`, `persistence_failed`, and `retention_full`.

Exact observation success replay returns `already_reconciled`. Exact edge success replay
returns the recorded `edge_only_completed` terminal result with `idempotent_replay: true` and
does not invoke the edge adapter again.

No envelope includes secret values, source contents, raw argv, environment values, raw
configuration, or private paths.
