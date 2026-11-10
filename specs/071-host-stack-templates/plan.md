# Implementation Plan: Host Stack Templates

**Branch**: `feat/host-stack-templates` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/071-host-stack-templates/spec.md`

## Summary

Turn a committed `sandbox.hosting.yml` with a `stack:` block into a template that
`sb host stack up` renders per slug, checks out at a controller-resolved commit
into a detached Git worktree, and delivers through the unchanged durable
`job-start` + `host apply` path. A controller-side stack registry owns slugs,
named loopback ports (18100-18199) and the latest submission. Two guards are added
to every apply: templates are refused (`stack_template_requires_stack_up`) and a
hostname held by another project on the remote is refused
(`hostname_owned_by_other_project`). `allowed_branches` gains fnmatch globs.

## Technical Context

**Language/Version**: Python 3.11+ (stdlib + PyYAML, as the rest of `sandbox/`)

**Primary Dependencies**: existing `sandbox.core._hosting`, `sandbox.commands.hosting`,
`sandbox.hosting.destroy`, `sb job-start`, `sandbox.jobs.registry.JobRepository`, Git CLI

**Storage**: controller JSON file `$SANDBOX_HOME/runtime/stacks/registry.json`
(0600, flock-serialized); rendered manifests and worktrees under
`$SANDBOX_HOME/runtime/stacks/<slug>/`; hosting state gains a `hostnames` list
per target

**Testing**: `unittest` suites under `tests/`, run by the Haiku test helper

**Target Platform**: macOS/Linux controller; Linux remote reached over the registered SSH transport

**Project Type**: CLI (`sb host stack up|list|down`)

**Performance Goals**: `up --plan` completes with one read-only remote probe

**Constraints**: no new remote mutation path; every existing apply guard stays;
plan changes nothing; placeholder hosts only in code/tests/docs

**Scale/Scope**: tens of stacks per remote (100-port range)

## Constitution Check

- **I/II (per-project, registry)**: stacks are hosting targets, not WordPress
  instances; the instance registry is untouched. Stack records are their own
  controller store, read only through the new module. PASS.
- **III (single entry, modular package)**: logic lives in
  `sandbox/hosting/stack_template.py`, `sandbox/hosting/stack_registry.py`,
  `sandbox/hosting/hostname_ownership.py`, `sandbox/commands/host_stack.py`;
  dispatch is a new `host` action, no new top-level command. PASS.
- **IV (live proof)**: a live `stack up --confirm` needs owner authorization and is
  out of scope for this branch; CLI runs against a synthetic remote plus a
  read-only `--plan` are the available evidence. Reported as a limit. PASS with
  stated limit.
- **V (idempotency, docs with code)**: re-running `up` reuses ports and the
  rendered file is deterministic; `down` tolerates an absent target. Docs updated
  in the same change. PASS.
- **VI (parity before removal)**: nothing is removed; existing manifests and
  literal ports are untouched. New web-port allocations skip 18100-18199;
  existing recorded ports are kept. PASS.

## Project Structure

### Documentation (this feature)

```text
specs/071-host-stack-templates/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/cli.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/core/_hosting.py              # HostingRefusal, template refusal, rendered-manifest
                                      # acceptance, branch_allowed(), stack port range reserve,
                                      # validate_loaded() split
sandbox/hosting/stack_template.py     # types, template parse/validate, render
sandbox/hosting/stack_registry.py     # stack records, port allocation, flock store
sandbox/hosting/hostname_ownership.py # holdings + hostname_owned_by_other_project
sandbox/commands/host_stack.py        # up/list/down handlers: ref, worktree, probe, job-start
sandbox/commands/hosting.py           # dispatch, apply guards, glob branch policy, hostnames
sandbox/cli.py                        # `stack` action, up|list|down, --template/--name/--ref/--port/--plan
tests/test_host_stack.py              # template, render, registry, ownership, CLI
docs/remote-hosting.md, docs/sandbox-config-reference.md,
skills/sandbox-cli/SKILL.md (+ .agents mirror)
```

**Structure Decision**: pure template/registry/ownership logic in `sandbox/hosting/`
so it is testable without a remote; side effects (Git, SSH probe, job-start,
destroy) in `sandbox/commands/host_stack.py`.

## Complexity Tracking

None.
