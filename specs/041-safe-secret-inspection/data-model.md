# Data Model: Safe Secret Inspection

## RegisteredSource

Represents one approved secret-bearing assignment file.

| Field | Meaning | Validation |
|---|---|---|
| `alias` | Stable caller-facing identity | Portable lowercase slug; unique within personal/project scope |
| `scope` | `personal` or `project` | Personal is built-in; project entries are explicit config |
| `relative_path` | Project-relative `.env*` location | No absolute path or traversal; canonical target remains in project root |
| `mcp_modes` | Allowed MCP operations | Subset of `keys`, `metadata`, `validate`, `masked` |
| `max_bytes` | Read bound | Fixed v1 maximum 1 MiB |

The resolved filesystem path is internal and never returned. A source transitions from `registered` to `opened` only after descriptor checks; any link/type/owner/mode/size/race failure produces `refused`.

## ParsedDocument

Bounded inert representation of one source.

| Field | Meaning |
|---|---|
| `raw_bytes` | Internal original bytes retained only for parsing/update |
| `newline_style` | LF or CRLF; mixed endings are refused for writes |
| `records` | Ordered comments, blanks, and assignments |
| `entries` | Key-to-assignment index after duplicate detection |
| `revision` | Opaque keyed identifier for concurrency checks |

No document field is serialized into public results.

## SecretEntry

| Field | Meaning | Public exposure |
|---|---|---|
| `key` | Validated assignment name | Yes, when selected/authorized |
| `value` | Internal bytes/text | Reveal TTY or selected child only |
| `state` | missing/empty/present/multiline/structured/unsupported | Yes |
| `length_bucket` | Fixed bucket | Yes |
| `classification` | recognized opaque/protected/unrecognized | Yes only as bounded class |

The raw value is deliberately excluded from generic representations, errors, audit, and result models.

## ValidationProfile

| Field | Meaning |
|---|---|
| `name` | Stable reviewed identifier |
| `public_prefixes` | Provider/type prefixes documented as public |
| `length_rule` | Exact or bounded internal length check |
| `character_rule` | Allowed character set/class check |
| `segment_rule` | Optional internal structural check |
| `expiry_rule` | Optional embedded expiry check |
| `mask_eligible` | Whether a successful value can use recognized masking |

Validation states are `pass`, `fail`, or `not_applicable`; all results carry `live_checked=false`.

## DisclosureResult

Transport-neutral result containing only explicitly permitted fields:

- operation and source alias;
- selected key names;
- state, length bucket, validation checks, or fixed mask according to mode;
- safe reason code, success state, counts, and opaque revision when relevant;
- never a generic value field.

## UseProfile

| Field | Meaning | Validation |
|---|---|---|
| `name` | Registered profile identity | Unique safe slug |
| `source` / `key` | Fixed approved credential | Must reference registered source |
| `argv` | Direct command and fixed arguments | Non-empty strings; no implicit shell |
| `destination` | Approved child environment name or future non-env channel | Portable name; dangerous names denied |
| `timeout_seconds` | Child wall-time bound | 1–1800 |
| `max_output_bytes` | Combined redacted output | 1–1,048,576 |
| `mcp` | Explicit MCP eligibility | Defaults false |

Use state transitions: `authorized` → `launched` → `exited`, `timed_out`, or `failed`; every transition emits non-secret audit metadata.

## UpdateRequest

| Field | Meaning |
|---|---|
| `source` / `key` | Single target |
| `intent` | create, replace, or either |
| `input_channel` | tty, stdin, registered reference, or generator |
| `expected_revision` | Optional concurrency precondition |
| `validation_profile` | Optional required shape check |
| `request_id` | Short-lived coordination identity, never bearer authority |

Update state transitions: `prepared` → `input_received` → `validated` → `committed`; failures become `refused` or `failed` without exposing input or prior state.

## AuditEvent

| Field | Meaning |
|---|---|
| `schema_version` | Audit format version |
| `event_id` | Random event identity |
| `correlation_id` | Links intent and outcome |
| `phase` | intent or outcome |
| `operation` | keys/metadata/validate/masked/use/set/reveal |
| `source` / `keys` | Approved aliases/names |
| `surface` | cli or mcp |
| `actor` | Local/session identity when available |
| `profile` | Validation/use profile when relevant |
| `decision` | allowed/refused/failed/succeeded |
| `reason_code` | Bounded enumerated reason |
| `at` | UTC timestamp |
| `revision` | Opaque revision when relevant |

Forbidden fields include values, previews, exact lengths, hashes of values, source excerpts, child output, and input bytes.
