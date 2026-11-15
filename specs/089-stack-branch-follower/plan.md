# Implementation Plan: Stack branch follower

**Branch**: `feat/stack-branch-follower` | **Date**: 2026-11-15 | **Spec**: [spec.md](spec.md)

## Summary

A server-side follower for hosted stacks. The owner declares a follow on a joined
remote with `sb host follow add`; the remote stores it, installs one systemd user timer
and generates read-only GitHub deploy keys. Every 5 minutes the timer runs
`sb host follow tick --here`, which fetches the tracked branches, decides, and on a
change submits one top-level server CLI durable job `host follow apply`. That job runs
the declared prepare step and the same `host stack up` plan + confirm code a person's
dispatched stack up runs, then waits for the nested apply job and records the outcome.

## Technical Context

**Language/Version**: Python 3.11+ (stdlib plus existing Sandbox modules)
**Primary Dependencies**: `sandbox.application.server_cli` (capabilities, owner env),
`sandbox.commands.host_stack` (`_up`, `_submit`, `job_view`), `sandbox.hosting.target_lease`
(lease path), `sandbox.hosting.stack_registry`, `sandbox.resources.schedule` and
`sandbox.resources.cleanup_routine.units` (unit write/rollback, host checks)
**Storage**: `$SANDBOX_HOME/runtime/hosting/follow/` (0700): `follows/<name>.json`,
`audit.jsonl`, `keys/<owner>__<repo>[.pub]`, `known_hosts`, `work/<name>/repos/<repo>`,
`locks/`
**Testing**: unittest, remote durable jobs on xspeed-dev only
**Target Platform**: Linux remote with systemd user manager and lingering
**Constraints**: no shell for owner input; private key never read by Sandbox; bounded
subprocess times; no parent environment passthrough in tests

## Constitution Check

- Module boundaries: a new package `sandbox/hosting/follow/` and a command module
  `sandbox/commands/host_follow.py` routed from `cmd_host`; no new consumers of
  `sandbox_core.py`, `COMMANDS`, or registry JSON outside the existing registry API
  (`stack_registry.read()`).
- Capability checks before side effects: joined-remote / server CLI context checks run
  before any store, key or unit write.
- Shared services own mechanisms: unit writes reuse `schedule.write_unit` and
  `snapshot_installation`/`restore_installation`; host checks reuse
  `cleanup_routine.units.check_host`.
- Docs with code: `docs/remote-hosting.md` section, CHANGELOG Unreleased line.

## Design

1. **Dispatch.** `host follow add|status|pause|resume|remove|run-now|key` join the
   `_dispatch_host_if_selected` set, so a joined `--remote` runs them as a server CLI job
   on the remote. Inside that job the handler sees `selected_owner_target(remote)` and
   operates on the local follow store; `add` records the job's `origin_remote_name` and
   its routing metadata (`front_door`, `mcp_port`) for later top-level submissions.
   Without that context (non-joined remote, or a controller-local call) the command is
   refused with `follow_requires_joined_remote`. `tick` and `apply` are internal: `tick`
   needs `--here`, `apply` needs its own server CLI job.
2. **Tick** (`sandbox/hosting/follow/tick.py`): host lock (non-blocking); for each
   follow, finalize a finished attempt from its job lifecycle, fetch, call the pure
   `decide()`, and on `start` record the attempt with its exact job-start argv, check out
   heads, and submit through `host_stack._submit`. A `submitting` attempt is replayed
   with the identical argv and request id only.
3. **Apply** (`sandbox/hosting/follow/apply.py`): in its server CLI job; apply lock;
   attempt and heads check; prepare (argv, placeholders `{dir}`, `{repo:NAME}`,
   `{commit:NAME}`, owner environment, time limit); `host_stack._up` with `plan` to read
   hostnames (refuse two-label apex names), then `confirm`; poll `job_view` of the nested
   apply job until terminal; record outcome.
4. **Git** (`sandbox/hosting/follow/git.py`): `ssh-keygen -t ed25519 -N ''` into an
   owner-only directory; `GIT_SSH_COMMAND` with `-i key -o IdentitiesOnly=yes -o
   IdentityAgent=none -o UserKnownHostsFile=<pinned> -o StrictHostKeyChecking=yes -o
   BatchMode=yes`; `git init` + fetch of one branch; `checkout --detach --force`.
5. **Units** (`sandbox/hosting/follow/units.py`): `sandbox-stack-follow.{service,timer}`,
   `OnBootSec=2min`, `OnUnitActiveSec=5min`, `RandomizedDelaySec=30s`, oneshot service
   with a backstop timeout.

## Project Structure

```text
sandbox/hosting/follow/__init__.py
sandbox/hosting/follow/spec.py      # declaration parsing and validation
sandbox/hosting/follow/store.py     # follow files, locks, audit
sandbox/hosting/follow/git.py       # keys, pinned host, fetch, checkout
sandbox/hosting/follow/units.py     # timer render/install/remove
sandbox/hosting/follow/tick.py      # poll and decide
sandbox/hosting/follow/apply.py     # the durable attempt body
sandbox/commands/host_follow.py     # CLI handler
sandbox/cli.py                      # `follow` action and flags
sandbox/commands/hosting.py         # route `follow`
sandbox/hosting/server_cli_dispatch.py  # select follow actions for joined dispatch
tests/test_host_follow.py
```

## Complexity Tracking

| Choice | Why | Simpler alternative rejected |
|--------|-----|------------------------------|
| Top-level server CLI job from the tick | Same job contract and evidence as a controller dispatch, with no controller | Running stack up in the timer process would have no durable evidence and no owner target |
| In-process `_up` inside the apply job | Identical code to a dispatched `host stack up`; its apply job is the nested child | A nested stack-up job would need output parsing for its apply job id |
