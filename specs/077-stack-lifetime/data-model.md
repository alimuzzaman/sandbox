# Data Model: Stack Lifetime and Per-Copy Stack Identity

## Enums

| Name | Values |
|---|---|
| Lifetime | `temporary`, `hosted`, `permanent` |
| LifetimeSource | `default`, `flag`, `classified` |
| RecordState | `active`, `reaping`, `reaped` |
| ReapReason | `expired`, `merged` |

## TargetRecord (remote, `runtime/target-lifetimes/<compose_project>.json`)

Flat, agreed with the xspeed-dev orchestrator (final):

```json
{
  "schema_version": 1,
  "compose_project": "sandbox-host-<project>-<env>",
  "project": "<project>", "environment": "<env>",
  "kind": "stack | target",
  "lifetime": "temporary", "lifetime_source": "default",
  "ttl_seconds": 1209600, "expires_at": "2026-11-24T10:00:00Z", "extended_count": 0,
  "state": "active",
  "owner": {"label": "Alice Example", "controller": "<32 hex>"},
  "created_at": "...Z", "updated_at": "...Z",
  "classified_by": null, "classified_at": null,
  "slug": "demo", "copy_id": "<8 hex>", "repo": "github.com/org/repo",
  "ref": "stack/demo", "ref_branch": "stack/demo", "commit": "<40 hex>",
  "dev_branch": "dev", "canonical": false, "template": "<abs path>",
  "ports": {"mailpit": 18142},
  "front_door": "nginx", "dns_mode": "wildcard | exact | unknown",
  "hostnames": ["hub-demo.example.test"],
  "merge": {"merged": true, "dev_branch": "dev", "dev_commit": "<40 hex>",
            "checked_at": "...Z", "controller": "<32 hex>", "commit": "<40 hex>"},
  "reap": {"eligible": false, "reason": null, "merged_into": null, "merged_at": null},
  "reap_run": {"run_id": "...", "started_at": "...Z", "reason": "expired",
               "finished_at": "...Z", "dns_left": false},
  "overrides": [{"action": "down|ttl|classify", "by": {"label": "...", "controller": "..."},
                 "at": "...Z", "owner": "..."}]
}
```

- `reap` is derived: written as of `updated_at`, recomputed at read time by `list`.
  `reap.reason` is `expired`, `merged` or null.
- A `kind: target` record (classified from a plain `host apply`) has null `slug`,
  `copy_id`, `ref`, `ref_branch`, `dev_branch`, `template`, empty `ports`,
  `canonical: false`, `commit`/`repo` when known, `lifetime_source: classified`,
  and `classified_by` (owner shape) plus `classified_at`.
- `owner.label` is a name (`--owner` or git `user.name`); an email is refused.
- `extended_count` counts TTL changes made with `stack ttl`.

Validation (parse at the boundary; any failure = invalid = protected):

- `schema_version == 1`; `compose_project == "sandbox-host-" + project + "-" + environment`;
  project and environment match `TARGET_PART_RE`; the file name equals
  `compose_project + ".json"`.
- `lifetime` in Lifetime; `temporary` requires integer `ttl_seconds` in
  [3600, 90 days] and an RFC3339 `expires_at`; `hosted`/`permanent` have both null.
- A hosted stack is canonical and its `ref_branch == dev_branch`; at most one
  canonical stack per (`repo`, `dev_branch`) on a remote.
- `state` in RecordState; `reaping`/`reaped` require `reap_run`.
- Unknown top-level keys are kept on rewrite.
- Writes: temp file in the same directory, fsync, rename, fsync of the directory.

## Eligibility (pure; host and controller)

```
eligible(record, now) =
  record valid and lifetime == temporary and state in {active, reaping}
  and (now >= expires_at -> "expired"  or  merge.merged and merge.commit == stack.commit -> "merged")
```

Hosted, permanent, invalid, unclassified (no file) and `reaped` are never eligible.

## Policy (remote, `policy.json`)

`{"schema_version": 1, "per_owner": 5, "total": 40, "min_free_bytes": 21474836480}`;
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
