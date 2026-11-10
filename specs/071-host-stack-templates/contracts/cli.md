# CLI Contract: `sb host stack`

```
sb host stack up   --template <checkout>/sandbox.hosting.yml --name <slug> [--ref <branch|commit>]
                   [--port NAME=N ...] [--environment E] [--request-id ID] --remote R (--plan | --confirm) [--json]
sb host stack list --remote R [--json]
sb host stack down --name <slug> --remote R --confirm [--lock-wait S] [--json]
```

## JSON results

`up --plan` / `up --confirm`:

```json
{"ok": true, "action": "stack-up", "mode": "plan|confirm", "slug": "demo", "remote": "R",
 "project": "xspeed-hub-demo", "environment": "sandbox", "ref": "stack/demo",
 "ref_branch": "stack/demo", "commit": "<sha>", "hostnames": ["..."],
 "ports": {"mailpit": 18142}, "manifest": "<path>", "source_root": "<path or null in plan>",
 "submission": ["<sb>", "job-start", "..."], "job_id": "<confirm only>", "request_id": "..."}
```

`list`: `{"ok": true, "action": "stack-list", "remote": "R", "stacks": [StackRecord view]}`
with `last_apply` `{status, at, job_id, request_id}`.

`down`: `{"ok": true, "action": "stack-down", "slug", "remote", "project", "environment",
"released_ports": {...}, "destroy": <destroy result>}`.

Refusal: `{"ok": false, "code": "<code>", "error": "<message>"}`, exit 1.

## Codes

| Code | When |
|---|---|
| `stack_template_requires_stack_up` | plain host command on a template |
| `stack_template_invalid` | schema/placeholder error in the template or its render |
| `stack_slug_invalid` | slug fails `stack.slug` |
| `stack_ref_unresolved` | `--ref` does not name a commit |
| `stack_branch_unavailable` | `allowed_branches` renders empty (detached `{ref_branch}`) |
| `stack_port_invalid` | bad `--port` syntax, undeclared name, out of range |
| `stack_port_unavailable` | pinned/allocated port bound by something else or owned by another stack; range exhausted |
| `stack_port_probe_unavailable` | the read-only remote probe failed |
| `stack_slug_in_use` | slug recorded for a different remote or project |
| `stack_apply_in_progress` | previous stack job still active |
| `stack_not_found` | `down` for an unknown slug on that remote |
| `stack_submission_failed` | job-start failed or returned no job id |
| `stack_destroy_failed` | destroy refused/failed; ports kept |
| `hostname_owned_by_other_project` | any apply/up whose hostname another project holds on the remote |
| `stack_confirmation_required` | `up` without exactly one of `--plan`/`--confirm`, `down` without `--confirm` |
