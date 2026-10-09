# Data Model: Hosted Delivery Evidence Reconciliation

## Delivery identity (hosted)

`scope = digest({target_kind: hosted, remote_name, project_name, environment})`; operation document gains `evidence: {checkout_root, project_identity, declaration_digest}`.

## Tables (delivery SQLite)

| Table | Columns | Rules |
|---|---|---|
| `attempt_phase_evidence` | operation_id, phase (`transfer`\|`edge`\|`dns`\|`compose`\|`initializers`\|`edge_rollback`\|`dns_rollback`), state (`started`\|`finished`\|`complete`\|`incomplete`), at | append-only |
| `attempt_preattempt` | operation_id PK, revision, config_digest, observed_at | written at admission |
| `attempt_reconciliations` | operation_id PK, result, observation_digest, evidence JSON (bounded 16 KiB), release_rule, original_transport_fact, at | at most one writing result per operation |
| `attempt_links` | operation_id PK, previous_operation_id | retry link |
| `request_bindings` | + `converted_to` (nullable) | alias after conversion |

## Reconciliation result

`{result: adopted_by_observation|no_effect_proven|insufficient_evidence|diverged|authority_pending|unavailable, request_id, observed_at, evidence:{revision_match, config_match, proofs:{edge, dns, initializers}, journal_unresolved, image_record?}, missing:[...], reason?, later_attempt?, release_rule?, wrote: bool}`.

## Job delivery outcome

Job record: `delivery_outcome: {request_id, state: succeeded|failed|uncertain|no_effect, reconciled_by?}`, `transport_events: [supervisor_lost, ...]`.

## Fence release rules (applied in order)

1. adopted or no_effect_proven → release.
2. source: `compose` never `started` ∧ (no edge/dns phase started ∨ `edge_rollback`/`dns_rollback` = `complete`) → release.
3. image: activation `refused` with no runtime effect entered ∧ (no edge rollback ∨ complete), or request record `committed`/`recovery_no_effect` corroborated → release.
4. otherwise keep; refusal names missing item and exits (`host recover`, `host retire-delivery`).
