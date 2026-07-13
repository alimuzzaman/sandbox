# Implementation Plan: Lenzora TODO Worker

**Branch**: `codex/hermes-public-access` | **Date**: 2026-07-13 | **Spec**: [spec.md](spec.md)

## Summary

Replace idle Lenzora Kanban dispatch with one bounded Terra/Medium worker that reads root `TODO.md`, works on only the first unchecked Markdown task, verifies it, and leaves changes uncommitted in a dedicated Lenzora worktree. The worker is allowed to return `NO_TODO_WORK` when the file is absent or complete.

## Technical Context

**Language/Version**: Python 3.14 for Sandbox control-plane tests and JSON catalog.

**Primary Dependencies**: Pinned Hermes Agent CLI, Git worktrees, systemd user scheduler.

**Storage**: Committed `sandbox/hermes/cron-catalog.json`; remote Hermes cron inventory and dedicated worktree.

**Testing**: Python `unittest`, catalog-integrity tests, live `cron verify`, health, and reconciliation.

**Target Platform**: Managed Linux Hermes server.

**Constraints**: One unchecked task/run; clean isolated worktree; no commit, push, deployment, credential change, deletion, or external action; no background mutation from missing/empty TODO state.

## Constitution Check

- The command remains in an importable Sandbox module and uses committed desired state.
- Remote mutation remains behind explicit confirmed reconciliation and is reversible through the protected Hermes jobs backup.
- The worker holds one owner/worktree per task queue and preserves all changes for human review.
- Documentation, tests, and catalog change together. **Pass.**

## Design

1. Generalize the managed catalog-worktree preparation helper from the existing Sandbox agent to a named catalog agent with a repository field inferred from its workdir template. It must create or fast-forward only a clean dedicated worktree while holding existing tick/worktree locks.
2. Set quota requeue, Kanban dispatch, and Sandbox task worker to disabled in the committed catalog. Add one enabled `lenzora-todo-task` Terra/Medium agent on the existing conservative four-hour cadence.
3. Its guarded prompt checks root `TODO.md`, returns `NO_TODO_WORK` if absent/completed, selects the first `- [ ]` task otherwise, performs only that bounded task, runs task-relevant checks, marks the selected checkbox only after success, and leaves the isolated worktree uncommitted.
4. Reconcile exactly once after server synchronization; verify the no-work terminal result because no Lenzora `TODO.md` is currently present.

## Project Structure

```text
sandbox/core/_hermes.py                 # managed catalog worktree preparation
sandbox/hermes/cron-catalog.json        # desired active/disabled jobs and guarded prompt
tests/test_hermes.py                    # reconciliation and worktree tests
tests/test_hermes_catalog_integrity.py  # catalog state tests
docs/hermes-agent.md                    # operating policy
specs/026-lenzora-todo-worker/          # feature artifacts and live evidence
```

## Complexity Tracking

No constitution exception is required. The generic worktree helper replaces a Sandbox-specific branch rather than adding a parallel control path.
