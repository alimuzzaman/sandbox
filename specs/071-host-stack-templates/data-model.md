# Data Model: Host Stack Templates

## StackTemplate (parsed, in memory)

| Field | Type | Rule |
|---|---|---|
| slug_pattern | compiled regex | from `stack.slug`; full match required |
| ports | tuple[str] | from `stack.ports`; unique, `^[a-z][a-z0-9-]{0,30}$` |
| raw | dict | manifest without `stack` |

## StackRef

| Field | Type | Rule |
|---|---|---|
| ref | str | as given (default `HEAD`) |
| commit | str | 40 lowercase hex |
| ref_branch | str | branch name without remote prefix; `""` when detached |

## Rendered manifest (`runtime/stacks/<slug>/sandbox.hosting.yml`)

The template with placeholders substituted, `stack` removed, `source_root` absolute, plus:

```yaml
stack_instance:
  slug: demo
  ref: stack/demo
  ref_branch: stack/demo
  commit: <40 hex>
  template: <absolute template path in the operator checkout>
  ports: {mailpit: 18142}
```

## StackRecord (registry `runtime/stacks/registry.json`)

```json
{"version": 1, "stacks": {"<remote>/<slug>": {
  "remote": "R", "slug": "demo", "project": "xspeed-hub-demo", "environment": "sandbox",
  "template": "/abs/checkout/sandbox.hosting.yml", "checkout": "/abs/checkout",
  "ref": "stack/demo", "ref_branch": "stack/demo", "commit": "<sha>",
  "hostnames": ["hub-demo.example.test", "..."], "ports": {"mailpit": 18142},
  "manifest": "/abs/runtime/stacks/demo/sandbox.hosting.yml",
  "worktrees": ["/abs/runtime/stacks/demo/source/<sha12>-<token>"],
  "last_apply": {"request_id": "...", "job_id": "...", "submitted_at": 1762790400,
                 "status": "submitted"}
}}}
```

- Key `<remote>/<slug>`; a slug is unique per controller across remotes.
- `ports` is set on the first `up` and only changed by a `--port` pin; removed only by `down`.
- `last_apply.status` is refined at read time from the durable job (`accepted|queued|running|cancelling|succeeded|failed|timed_out|cancelled|interrupted`).

## Hosting state addition

`state["hosts"]["<remote>/<project>/<env>"]["hostnames"]`: sorted list of the
target's route hostnames, written when an apply is accepted.

## State transitions

`absent → recorded (up --confirm) → submitted → (job terminal) → recorded → down → absent`.
`up` is refused while the last job is `accepted|queued|running|cancelling`.
