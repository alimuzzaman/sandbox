# Data Model: Reclaim Stray Unlabeled Containers

## StrayAssessment (returned by `assess_stray_container`)

| Field | Type | Notes |
|---|---|---|
| `owned` | bool | True only when every rule in research R2-R3 holds |
| `reason` | str or null | First protecting rule when not owned: `invalid_record`, `compose_project`, `sandbox_identity`, `no_mounts`, `named_volume`, `unsupported_mount`, `bind_outside_checkout`, `non_default_network`, `restart_policy` |
| `evidence` | object | `id`, `name`, `image`, `created`, `started_at`, `restart_count`, `running`, `network_mode`, `networks` (sorted), `mounts` (list of `{type, source, destination}`) |
| `fingerprint` | str | sha256 hex of the fields in research R4 |

## Probe container record (entry `containers[]`)

Existing `{id, name, running, project}` plus `stray` (a StrayAssessment) when the
entry is a workspace checkout. Records are de-duplicated by `id` per entry.

## ReclaimCandidate additions

| Field | Type | Notes |
|---|---|---|
| `remove_containers` | tuple of dict | One `{id, name, fingerprint, evidence}` per owned stray; empty for a workspace candidate with none |

`evidence_digest` includes the stray fingerprints when there are any.

## Planner skip row additions

`protected_containers`: list of `{id, name, reason}` for a workspace kept because a
bound container is not owned; `reason` of the row is `bound_container_protected`.
Container reasons add `foreign_compose_project`; a row with more than
`MAX_STRAYS_PER_CHECKOUT` owned strays has reason `too_many_stray_containers`. A
record without `stray` evidence (an older probe) keeps today's behaviour: it is
listed in `stop_containers` only and the host refuses it as before.

## Teardown result additions

| Field | Notes |
|---|---|
| `strays_removed` | ids removed or found absent |
| `failures` | adds `container_evidence_changed:<id>`, `unplanned_bound_container:<id>`, `invalid_planned_strays`, `stray_inspect_failed:<id>`, `stray_stop_failed:<id>`, `stray_still_running:<id>`, `stray_rm_failed:<id>`, `bound_list_failed`, `bound_inspect_failed` |

## Manifest intent (stray)

`{"schema": 1, "run_id", "seq", "phase": "intent", "path": <container id>, "kind":
"stray_container", "workspace": <checkout>, "fingerprint", "at"}` written before
`docker stop`.

## State per planned stray at cleanup

`planned` → (`absent` | `matched` | `changed`) → for `matched`: re-inspected →
`intent recorded` → `stopped` → re-inspected → `removed`. Any `changed`, or an unplanned bound unlabeled container, ends
the candidate with no mutation; a stop or remove failure ends it with the checkout kept.
