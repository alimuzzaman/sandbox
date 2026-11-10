# Data Model: Stack Lifetime and Per-Copy Stack Identity

## Enums

| Name | Values |
|---|---|
| Lifetime | `temporary`, `hosted`, `permanent` |
| LifetimeSource | `default`, `flag`, `classified` |
| RecordState | `active`, `reaping`, `reaped` |
| ReapReason | `expired`, `merged` |

## TargetRecord (remote, `runtime/target-lifetimes/<compose_project>.json`)

```json
{
  "schema": 1,
  "compose_project": "sandbox-host-<project>-<env>",
  "project": "<project>", "environment": "<env>",
  "kind": "stack | target",
  "lifetime": "temporary", "lifetime_source": "default",
  "ttl_seconds": 1209600, "expires_at": "2026-11-24T10:00:00Z",
  "state": "active",
  "owner": {"label": "dev@example.test", "controller": "<32 hex>"},
  "created_at": "...Z", "updated_at": "...Z",
  "front_door": "nginx",
  "dns_mode": "wildcard | exact | unknown",
  "hostnames": ["hub-demo.example.test"],
  "stack": {
    "slug": "demo", "copy_id": "<8 hex>", "repo": "github.com/org/repo",
    "ref": "stack/demo", "ref_branch": "stack/demo", "commit": "<40 hex>",
    "dev_branch": "dev", "canonical": false, "template": "<abs path>",
    "ports": {"mailpit": 18142}
  },
  "merge": {"merged": true, "dev_branch": "dev", "dev_commit": "<40 hex>",
            "checked_at": "...Z", "controller": "<32 hex>", "commit": "<40 hex>"},
  "reap": {"run_id": "...", "started_at": "...Z", "reason": "expired",
           "finished_at": "...Z", "dns_left": false},
  "overrides": [{"action": "down|ttl|lifetime", "by": {"label": "...", "controller": "..."}, "at": "...Z"}]
}
```

Validation (parse at the boundary; any failure = invalid = protected):

- `compose_project == "sandbox-host-" + project + "-" + environment`; project and
  environment match `TARGET_PART_RE`; the file name equals `compose_project + ".json"`.
- `lifetime` in Lifetime; `temporary` requires integer `ttl_seconds` in
  [3600, 90 days] and an RFC3339 `expires_at`; `hosted`/`permanent` have both null.
- `hosted` with `kind: stack` requires `stack.canonical == true` and
  `stack.ref_branch == stack.dev_branch`.
- `state` in RecordState; `reaping`/`reaped` require `reap`.
- Unknown top-level keys are kept on rewrite.

## Eligibility (pure; host and controller)

```
eligible(record, now) =
  record valid and lifetime == temporary and state in {active, reaping}
  and (now >= expires_at -> "expired"  or  merge.merged and merge.commit == stack.commit -> "merged")
```

Hosted, permanent, invalid, unclassified (no file) and `reaped` are never eligible.

## Policy (remote, `policy.json`)

`{"schema": 1, "per_owner": 5, "total": 40, "min_free_bytes": 21474836480}`;
missing keys take defaults.

## StackRecord additions (controller registry, version 1, additive)

`copy_id`, `owner` `{label, controller}`, `lifetime`, `lifetime_source`,
`ttl_seconds`, `expires_at`, `repo`, `dev_branch`, `canonical`.

## Template addition

`stack.dev_branch` (optional string, default `dev`, a valid branch name).

## State transitions

```
(no file) --claim--> active --ttl/lifetime/merge--> active
active --(eligible at reap)--> reaping --(teardown ok)--> reaped
reaping --(next run)--> reaping (resume) --> reaped
active|reaped --stack down (controller)--> (file removed)
reaped --claim by the same copy/owner--> active (fresh lifetime)
(no file) <--classify --clear-- active (classified)
```
