# Data Model: Hermes Authorization Controls

## Authorization Request

| Field | Rules |
|---|---|
| `id` | 16 lowercase hexadecimal characters, generated once |
| `job_name` | Exact catalog-managed job name |
| `scope` | Lowercase slug, 1–64 characters |
| `replay_origin` | Canonical HTTPS origin without credentials, path, query, or fragment |
| `rationale` | 1–500 characters; credential-like text rejected |
| `fingerprint` | SHA-256 of immutable reviewed fields |
| `status` | `pending`, `approved`, `expired`, or `superseded` |
| `created_at`, `expires_at` | UTC ISO-8601 timestamps |
| `approved_at` | UTC ISO-8601 timestamp when applicable |

## Authorization Audit Event

| Field | Rules |
|---|---|
| `request_id` | References the authorization request |
| `event` | `requested`, `approved`, `expired`, or `superseded` |
| `at` | UTC ISO-8601 timestamp |
| `fingerprint` | Request fingerprint at event time |

## Lifecycle

`pending → approved`; `pending → expired`; `pending → superseded`. No other transition is permitted. Approving edits the matching job prompt only after state validation succeeds.
