# Data Model: Supervised Long-Running Secret Session

Nothing in this feature is persisted except the existing audit events. All
entities below live in memory for one session.

## Session Request

Built by the CLI from `sb secrets run --session ...`.

| Field | Type | Rule |
|-------|------|------|
| `source` | str | Registered alias; unknown raises `source_unknown` before audit intent (`SourceRegistry.policy`). |
| `bindings` | list of (key, destination) | Same normalization as `run_many`: 1 to 100 pairs, unique keys, unique destinations, each destination passes `validate_destination` (spec 041 FR-035). |
| `argv` | list of str | Non-empty executable, no NUL, direct exec, no shell (as `run_with_secrets`). |
| `lifetime_seconds` | int | 1 to 43,200; default 28,800; decimal digits only on the CLI (`lifetime_invalid`). |
| `surface` | str | Must be `cli`; otherwise `command_denied` before audit intent. |

Validation order: argument parse, lifetime, option conflicts, terminal and
foreground check, signal handlers installed, bindings/destinations/source,
audit intent, source read, key lookup (`key_missing`), child launch.

## Session Lifetime

| Field | Type | Rule |
|-------|------|------|
| `lifetime_seconds` | int | From the request. |
| `wall_deadline` | float (epoch seconds) | `time.time()` at launch + lifetime; drives the displayed local end time. |
| `mono_start` | float | `time.monotonic()` at launch. |

Expired when `time.time() >= wall_deadline` or
`time.monotonic() - mono_start >= lifetime_seconds`.

## Session Signals

| Field | Type | Rule |
|-------|------|------|
| `reason` | str or None | First recorded of `interrupted`, `hangup`; later signals do not overwrite it. |
| handled signals | set | `SIGINT` to `interrupted`, `SIGHUP` to `hangup`, `SIGTERM` and `SIGQUIT` to `interrupted`; `SIGTSTP` ignored. |
| previous dispositions | map | Restored when the context exits. |

## Session State (one session)

```text
validating -> refused                        (no read, no child)
validating -> audited -> reading -> refused  (key_missing; no child)
audited -> reading -> launching -> running
running -> ending(reason) -> ended
launching -> ending(reason)                  (signal recorded before launch; no child started)
```

`reason` is set by the first of: lifetime expiry, a recorded signal, a failed
display write (`hangup`), or the child's own exit (`child_exited`). `ending`
runs the group termination sequence (polite request, then forced, at most 5 s
total) and drains remaining output through the redactor.

## Session Result (`SessionResult`)

| Field | Type | In result | Rule |
|-------|------|-----------|------|
| `end_reason` | str | yes | One of `lifetime_expired`, `interrupted`, `hangup`, `child_exited`. |
| `exit_code` | int or None | yes | Child's return code when `child_exited`, else None. |
| `elapsed_seconds` | float | no | Only its class is exposed. |
| `elapsed_class` | str | yes | `under_1s`, `1_to_10s`, `10_to_60s`, `1_to_10m`, `10_to_60m`, `1_to_4h`, `4_to_8h`, `8_to_12h`, `12h_plus`. |
| `dropped_chunks` | int | yes | Output chunks dropped because redaction failed. |
| `lifetime_seconds` | int | yes | The requested lifetime. |
| `group_ended` | bool | yes | `true` when the child's process group was gone within the 5-second bound; `false` when a member the broker may not signal (privilege escalation inside the child) survived it. |

The service payload wraps it as
`{"ok": true, "operation": "run_session", "source", "key"|"keys", "result", "correlation_id", "reason_code"}`.
No output, value, length, hash or preview is ever a field.

## Session Audit Event

The existing `SecretAudit` intent/outcome pair, unchanged schema:

- intent: `operation="use_session"`, `source`, `keys`, `surface="cli"`, `decision="requested"`.
- outcome: same correlation id, `decision="succeeded"` with `reason_code` set to
  the end reason, or `decision="refused"`/`"failed"` with the refusal code.

## Broker exit status

| End | Exit |
|-----|------|
| `child_exited`, child status 0 | 0 |
| `child_exited`, child status 1 to 125 | same status |
| `child_exited`, any other status or death by signal | 1 |
| `lifetime_expired` | 0 |
| `interrupted` | 130 |
| `hangup` | 129 |
| refused start | 1, with `error: <code>: <message>` on stderr |
