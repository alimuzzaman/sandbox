# CLI Contract: lifetime and identity additions

All commands keep spec 071's envelope: success `{"ok": true, "action": ..., ...}`;
refusal `{"ok": false, "code": "<code>", "error": "<message>"}`, exit 1.

```
sb host stack up   --template T [--name SLUG] [--ref REF] [--lifetime temporary|hosted|permanent]
                   [--ttl 14d] [--owner LABEL] [--copy COPY_ID] [--port NAME=N ...]
                   [--environment E] --remote R (--plan | --confirm) [--json]
sb host stack list --remote R [--json]
sb host stack down --name SLUG --remote R --confirm [--force-owner] [--json]
sb host stack ttl  --name SLUG --remote R --ttl 14d [--lifetime L] [--force-owner] --confirm [--json]
sb host stack sweep  --remote R [--checkout PATH ...] [--confirm] [--json]
sb host stack reap   --remote R (--plan | --confirm) [--json]
sb host stack reap   --here (--plan | --confirm) [--json]          # on the host itself
sb host stack limits --remote R [--per-owner N] [--total N] [--min-free-gb N] [--confirm] [--json]
sb host classify --remote R --target PROJECT/ENV (--lifetime L [--ttl 14d] | --unclassify)
                 [--owner LABEL] [--force-owner] --confirm [--json]
sb resources cleanup --tier safe --remote R --confirm   # also runs the hosted pass
```

- `--ttl` accepts `<n>h`, `<n>d` or `<n>w`; 1 hour to 90 days. Default 14d.
- `--name` omitted on `up`: a generated slug (research R7) is reported in `slug`.
- `--lifetime hosted` requires the ref's branch to equal the template's
  `stack.dev_branch` (default `dev`); it marks the canonical stack.

## Additional result fields

`up` (plan and confirm) adds: `lifetime`, `lifetime_source`, `ttl_seconds`,
`expires_at`, `owner`, `copy_id`, `canonical`, `generated_name` (bool), and on a
confirmed canonical up `sweep` (the merge-sweep result).

`list` rows (from the remote, merged with this controller's registry) add:
`lifetime`, `lifetime_source`, `ttl_seconds`, `expires_at`, `owner`, `copy_id`,
`canonical`, `state`, `reap` `{eligible, reason, merged_into, merged_at}`,
`mine` (bool), `remote_record` (`present|missing|invalid`). The list also returns
`unclassified`: target compose projects on the remote with no record, and
`invalid`: record files that failed validation.

`ttl`: `{"ok": true, "action": "stack-ttl", "slug", "remote", "ttl_seconds", "expires_at", "lifetime"}`.

`sweep`: `{"ok": true, "action": "stack-sweep", "mode": "plan|confirm", "checked": [{"slug", "commit", "merged": true|false|null, "reason"}], "recorded": N}`.

`reap`: `{"ok": true, "action": "stack-reap", "mode", "run_id", "candidates": [...], "outcomes": [{"compose_project", "status": "reaped|skipped|failed", "reason", "removed": {...}}]}`.

`limits`: `{"ok": true, "action": "stack-limits", "policy": {...}, "mode": "show|confirm"}`.

`classify`: `{"ok": true, "action": "host-classify", "target", "record": {...} | null}`.

## Codes (new)

| Code | When |
|---|---|
| `stack_slug_taken` | slug held on the remote by another owner or another copy |
| `stack_project_taken` | the rendered Compose project is held by another record, or deployed with no record and not this controller's legacy stack |
| `stack_hosted_taken` | a canonical hosted stack for the repo already exists on the remote |
| `stack_lifetime_invalid` | hosted for a non-dev ref; bad `--lifetime` |
| `stack_ttl_invalid` | `--ttl` syntax or range |
| `stack_expired` | re-up of an expired temporary stack without `--ttl` |
| `stack_reaping` | the record is being reaped |
| `stack_not_owner` | `down`/`ttl`/`classify` on a record another owner holds without `--force-owner`; `--copy` with a different owner label |
| `stack_owner_unknown` | no `--owner` and no `git config user.name` |
| `stack_owner_invalid` | the owner label is an email address |
| `stack_limit_reached` | per-owner cap, remote-wide cap or free-disk minimum |
| `stack_record_unavailable` | the host store program failed or returned no result |
| `stack_record_invalid` | the record exists but fails validation (classify/ttl refuse it) |
| `stack_reap_unavailable` | `reap --remote` against a runtime without this feature |
| `target_not_found` | classify a target with no runtime directory on the remote |
| `stack_limits_invalid` | non-positive limits |
