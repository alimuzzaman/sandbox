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

**LastApply**: `status` (`applying`, `succeeded` or `failed`), `started_at` (int epoch), `finished_at` (int or null), `request_id` (str or null).

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
