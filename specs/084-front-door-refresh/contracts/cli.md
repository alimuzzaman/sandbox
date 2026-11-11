# Contract: CLI and MCP

## `sb host refresh-front-door`

```
sb host refresh-front-door --remote R (--target P/E | --name STACK | --project-dir DIR [--environment E])
                           [--confirm] [--json]
```

- Without `--confirm`: read-only plan. It resolves the target, checks every binding and
  privacy rule, reads the installed route strictly, renders the new route, and answers
  `action: plan` with the route digests. No lock beyond the reads, no audit line.
- With `--confirm`: the refresh. Exit 0 with `action: refreshed` or `unchanged`; exit 1
  with `{"ok": false, "code": ..., "error": ...}` (plus `restore` after a failed reload or
  verification).
- Never needs a durable job; never prints a secret, route text or address.

## `sb delivery inspect` additions

```
sb delivery inspect --remote R (--name STACK | --target P/E | --project-dir DIR (--environment E | --label L))
                    [--request-id ID | --operation-id ID] [--observe] [--limit N] [--json]
```

Resolves the target like `host status --name`, then reads in the delivery scope the
target's hosting record names, for one attempt only (`--request-id`, `--operation-id`, or
by default the request the record names); history and latest summaries are omitted and an
attempt of another hosted runtime refuses `binding_mismatch`. `--project-dir` keeps its
current behaviour.

## `sb host retire-delivery` additions

```
sb host retire-delivery --remote R (--name STACK | --target P/E | --project-dir DIR --environment E)
                        --original-request-id ID --confirm [--json]
```

When the attempt is not in the current checkout's scope, retire uses the scope the
target's hosting record names, only when that record names this exact request id.

## MCP `host_front_door_refresh`

`host_front_door_refresh(remote, target=None, name=None, project_dir=None, environment=None, confirm=False)`
returns the same JSON as the CLI with `--json`. It never raises.
