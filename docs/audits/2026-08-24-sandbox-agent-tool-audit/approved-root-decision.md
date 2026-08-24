# Approved audit roots

Decision date: 2026-08-24

Schema version: `audit-root-v1`

Manifest digest: `sha256:2eb606a67f63d6b6ac613c1c6f9189d42bbf470e8ca0cbfe1efe1c1e479e565b`

## Safe source reference

A Codex source reference is `CODEX-SRC-` plus the first 20 hexadecimal
characters of `sha256("sandbox-agent-tool-audit:v1:" + raw_id)`. The raw ID may
exist in memory only for an authorized join. The audit retains no reverse map.

## Root decisions

| Source label | Authority basis | Selection predicate | As of | Inventory unit | Schema |
| --- | --- | --- | --- | --- | --- |
| `CODEX-LOCAL-EXACT-CWD` | User-authorized, user-owned local Codex metadata | Include a JSONL ledger only when session metadata identifies `SANDBOX-CHECKOUT` as its exact working repository | `2026-08-24T16:47:19Z` | 549 included rollout files, 75 unique session IDs, and 3,351 metadata rows | `audit-root-v1` |
| `CLAUDE-SANDBOX` | User-authorized, normally readable local Claude metadata | Include JSONL only when the encoded project class identifies the Sandbox repository | `2026-08-24`, day precision | 1 source root, 28 JSONL files, and 13,990 records | `audit-root-v1` |
| `CLAUDE-T3-WORKTREE` | User-authorized, normally readable local Claude metadata | Include JSONL only when the encoded project class identifies a T3 worktree | `2026-08-24`, day precision | 20 source roots, 36 JSONL files, and 23,037 records | `audit-root-v1` |
| `T3-SAFE-METADATA` | Explicit metadata-only authorization | Inspect safe filenames and file metadata only. Do not open or decode an application, browser, database, blob, cache, or storage record | `2026-08-24`, day precision | `behavioral_coverage_unavailable` | `audit-root-v1` |
| `HISTORICAL-CODEX-PATTERN` | Prior authorized exact-CWD pattern extraction | Apply the legacy exact-CWD August selection used by the broad pattern table | `2026-08`, exact timestamp unavailable | 473 included rollout files | `historical-v1` |

The historical pattern snapshot and the current Codex snapshot are separate
populations. Do not add or compare their counts without a validated crosswalk.

T3 application and browser stores cannot become parser inputs without a
supported owner export or share artifact and new Sol High and root authorization.

## Durable-output check

Run the full durable-output check from the repository root:

```text
docs/audits/2026-08-24-sandbox-agent-tool-audit/check-durable-artifacts.sh
```

The check scans durable Markdown, JSON, JSONL, and TSV content plus every
filename. It reports only an offending filename, never matching content. A
match fails the check.

The digest was derived from the five normalized decision rows in table order.
The canonical fields are source label, authority code, predicate code, as-of
value, inventory value, and schema version, separated by `|` and terminated by
one line feed.
