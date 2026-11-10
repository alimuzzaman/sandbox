# Data Model: Stacks and Hosted Sites View

All records are JSON and carry `schema: 1`. Readers validate each field at the boundary and drop any record that fails, reporting it in `partial_reasons`.

## TargetSummary (remote: `$SANDBOX_HOME/runtime/hosts/<project>/<environment>/summary.json`, mode 0600)

| Field | Type | Notes |
|---|---|---|
| schema | 1 | |
| remote | str | the remote name the controller used |
| project, environment | str | same validation as hosting (`TARGET_PART_RE`) |
| compose_project | str | `sandbox-host-<p>-<e>` |
| loopback_port | int | |
| healthcheck_path | str | starts with `/` |
| routes | list of Route | at most 64 |
| commit | str or null | 40-hex once source is published |
| last_apply | LastApply | |
| stack | StackFacts or null | |

**Route**: `hostname` (normalized; may start with `*.`), `mode` (`serve` or `redirect`), `primary` (bool), and `target` (str, redirect only).

**LastApply**: `status` (`applying`, `succeeded`, `unverified` or `failed`), `started_at` (int epoch), `finished_at` (int or null), `request_id` (str or null), `reason` (str or null; follow-up 2026-11-10: the delivery reason, set for `unverified` and, when known, `failed`).

**StackFacts**: `slug`, `ref`, `ref_branch`, `commit`, `template` (relative path), `ports` (name→int), `created_at` (int or null), and `ttl_expires_at` (int, optional, present only if the controller record has it).

Excluded on purpose: secrets, secret key names, derived environment, compose override text, Caddyfile, basic-auth data, absolute controller paths, worktrees.

State transitions: `applying` goes to `succeeded` (end of `_apply_host`) or to `failed` (any exception after `started`). A later apply starts again at `applying`.

## SiteLink (remote: `$SANDBOX_HOME/runtime/stack-sites/<slug>/<hostname>.json`, mode 0600)

| Field | Type |
|---|---|
| schema | 1 |
| slug | str (stack slug pattern) |
| hostname | str (normalized, no wildcard) |
| instance | str |
| label | str |
| commit | str or null |
| recorded_at | int |

## SiteLinkRef (controller: `StackRecord.sites`)

`{hostname, instance, label, commit, recorded_at}`. Replaced by hostname (one entry per hostname). `sb host stack up` keeps the list.

## View model (`GET /api/hosting`)

```text
HostingView
  ok: true
  generated_at: int
  sources: ["this-server" | "controller"]
  stacks: [StackView]
  targets: [TargetView]        # plain hosted targets
  partial_reasons: [str]

StackView
  key: "<remote>/<slug>"; remote; source; slug; project; environment
  ref; ref_branch; commit; template; ports{name:int}; mailpit_port: int|null
  created_at; ttl_expires_at|null
  last_apply: {status, started_at, finished_at, request_id}
  servers: [ServerRow]

TargetView
  key: "<remote>/<project>/<environment>"; remote; source; project; environment
  commit; last_apply; recorded: bool  (false = legacy dir without summary)
  servers: [ServerRow]

ServerRow
  hostname; url|null (null for wildcard); origin: "route" | "linked" | "matched_by_name"
  mode: "serve" | "redirect"; target|null; primary: bool
  state: "serving" | "redirecting" | "applying" | "failed" | "instance_not_found" | "unknown"
  ref: {branch|null, commit|null}
  instance|null
  health: {status: "checking"|"up"|"up_login"|"redirect"|"client_error"|"server_error"|"unreachable"|"not_checked",
           http_status|null, checked_at|null}
```

Row order: primary route, other routes by hostname, linked sites by hostname, then name-matched sites by hostname.

## Follow-up 2026-11-10

**Apply outcome** (from the delivery summary the apply returns): `execution_state` other than `succeeded` maps to `failed`. With `execution_state == succeeded`, `delivery_state == succeeded` maps to `succeeded`, and `incomplete`/`unknown` map to `unverified` with `reason`. `delivery_state == failed` maps to `failed` with `reason`. With no delivery summary, the apply's bool decides `succeeded` or `failed`.

**LifetimeView** (one per stack, target and inventory row; projected from `hoststore.view`):

```text
LifetimeView
  classification: "recorded" | "unclassified" | "unknown"   # unknown: the source could not be read yet
  reason: null | "no_record" | "invalid" | "unavailable" | "loading"
  lifetime: "temporary" | "hosted" | "permanent" | null
  owner: str | null
  expires_at: int | null            # epoch; temporary only
  seconds_left: int | null          # expires_at - generated_at, may be negative
  canonical: bool
  reap_eligible: bool               # false unless classification == "recorded"
  reap_reason: "expired" | "merged" | null
  state: "active" | "reaping" | "reaped" | null
  extended_count: int
  kind: "stack" | "target" | null; slug|null; copy_id|null; ref_branch|null; commit|null
```

Rule: anything other than a record that `hoststore.parse_record` accepts gives `classification` `unclassified` (`reason` `no_record` or `invalid`) or `unknown`, with `reap_eligible` false.

**HostingView additions**:
- `StackView` and `TargetView` gain `compose_project` and `lifetime`. `TargetView` gains `stack_slug` (str or null), the lifetime record's slug for a target without a summary.
- A top-level `lifetimes: [LifetimeRow]` lists one row per stack and target plus any lifetime record without a card. A `LifetimeRow` is a `LifetimeView` plus `remote`, `source`, `project`, `environment`, `compose_project`, `name` (slug, or `project · environment`) and `card` (bool).

Order: rank (temporary and reapable 0, temporary 1, hosted 2, permanent 3, unknown 4, unclassified 5), then `expires_at` ascending, then owner, then name.

**HostedTargetUsage additions** (host inventory rows): `lifetime` (LifetimeView), `last_apply_reason` (str or null), and `stack_slug` falling back to the lifetime record's slug.
