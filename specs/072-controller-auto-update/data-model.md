# Data Model: Controller Auto-Update

All types live in `sandbox/controller_update/models.py` as frozen dataclasses,
`Literal` unions and enums. External data (state file, probe output, git
output) is parsed into these types at the boundary; invalid values become
"absent", never a partly valid object.

## Revision

A 40-character lowercase hex git commit id. `parse_revision(text) -> str | None`
is the only constructor; anything else (short ids, uppercase, whitespace,
24-hex runtime digests) is `None`.

## Hint

| Field | Type | Rule |
|---|---|---|
| remote | `str` | registered remote name (`[a-z0-9_-]+`) |
| revision | `Revision` | parsed |
| source | `Literal["install", "probe"]` | how it was learned |
| recorded_at | `float` | epoch seconds |

One hint per remote; a newer record for the same remote replaces the old one,
even when its revision is older (the remote was rolled back).

## Refusal

| Field | Type | Rule |
|---|---|---|
| revision | `Revision` | |
| upstream | `str` | e.g. `origin/latest` |
| reason | `SkipReason` | `not_on_upstream`, `not_descendant`, `fetch_failed` |
| until | `float` | epoch seconds; 6 h ahead, 15 min for `fetch_failed` |

Matches only when revision and upstream both match and `until` is in the future.

## CheckoutState

| Field | Type |
|---|---|
| root | `Path` |
| head | `Revision` |
| branch | `str | None` (`None` = detached) |
| upstream_remote | `str | None` |
| upstream_branch | `str | None` (short name, e.g. `latest`) |
| origin_url | `str | None` (raw config value) |
| clean | `bool` (tracked files and index) |

`read_checkout(root) -> CheckoutState | None`; `None` means "not a git
checkout" and ends evaluation silently.

## SkipReason (enum)

`disabled`, `machine_output`, `job_child`, `reexec_guard`, `not_cli`,
`not_remote_command`, `no_hint`, `not_git`, `detached`, `no_upstream`,
`origin_not_github`, `dirty`, `locked`, `fetch_failed`, `not_on_upstream`,
`not_descendant`, `ff_failed`, `refresh_failed`, `rollback_failed`.

Each reason has a fixed `notice: bool` (whether it prints) and a fixed message
template in `contracts/cli-behavior.md`. The silent ones: `disabled`,
`machine_output`, `job_child`, `reexec_guard`, `not_cli`,
`not_remote_command`, `no_hint`, `not_git`, and any memoized refusal.

## Decision (union)

```text
NoAction(reason: SkipReason)                       # silent
Skip(reason: SkipReason, hint: Hint, notice: str)  # one stderr line
Updated(old: Revision, new: Revision, upstream: str, notices: tuple[str, ...])
RolledBack(old: Revision, attempted: Revision, notice: str)
Stranded(head: Revision, notice: str)              # rollback failed; exit 1
AlreadyMoved(new: Revision)                        # another process updated; re-exec
```

Transitions inside `apply()`:

```text
candidate --lock busy--> Skip(locked)
candidate --state changed under lock--> re-evaluate (once)
candidate --fetch fails--> Skip(fetch_failed) + memo 15 min
candidate --not on upstream / not descendant--> Skip + memo 6 h
candidate --ff fails--> Skip(ff_failed)
candidate --ff ok, refresh ok--> Updated  -> re-exec
candidate --ff ok, refresh fails, rollback ok--> RolledBack -> run on old code
candidate --ff ok, refresh fails, rollback fails--> Stranded -> exit 1
```

## Policy

| Field | Source |
|---|---|
| enabled | `SANDBOX_AUTO_UPDATE` if a recognised value, else `defaults.controller_auto_update` (bool, default `true`) |
| fetch_timeout | 15 s |
| git_timeout | 10 s |
| refresh_timeout | 300 s |
| refusal_ttl | 6 h |
| fetch_failure_ttl | 15 min |
| lock_wait | 2 s |
