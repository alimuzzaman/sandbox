# Implementation Plan: Shared node store and hardlinked git workspaces

**Branch**: `latest` (spec dir `044-shared-node-store-and-git-dedup`) | **Date**: 2026-08-16 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/044-shared-node-store-and-git-dedup/spec.md`

## Summary

Two structural leaks make remote host storage grow with every job. This plan closes both.

1. **Git object sharing.** `RemoteJobTransport._prepare_workspace` materializes a workspace
   with `cp -a "$source/." "$workspace"`, byte-copying the whole `.git`. Replace the single
   copy with a three-part copy: worktree by value, `.git` metadata by value, and
   `.git/objects` by hard link. Git never mutates an object or pack file in place — it
   creates new files and unlinks old ones — while everything git *does* mutate (`HEAD`,
   `index`, `refs`, `logs`, `packed-refs`, `config`, `COMMIT_EDITMSG`) stays a private copy.
   The same helper backs the local `workspace reset` copy in the workspace lifecycle.
2. **Shared package store.** The generic Compose adapter owns the overlay it writes for every
   instance. When a project opts in with `compose.nodeStore`, the overlay mounts **one
   family-scoped named volume** at `/sandbox-node` and exports `SANDBOX_NODE_STORE`,
   `SANDBOX_NODE_MODULES`, and `npm_config_store_dir`. The family is the runtime id with the
   `-workspace-<hash>` segment removed, so the source checkout and all of its workspaces
   resolve to the same volume instead of minting a new empty one per directory. Because both
   the store and a distinct dependency tree for each canonical runtime live inside that single
   mount, sibling workspaces cannot overwrite one another's dependency versions, `link()` does
   not return `EXDEV`, and pnpm can hard-link instead of copying. The hosted project consumes the contract by
   dropping its per-workspace `node_modules` volume and pointing `node_modules` at
   `$SANDBOX_NODE_MODULES`.

**uid resolution (explicit).** Containers keep running as their image account (root). The
shared store therefore must never land on the host bind: it lives in a Docker-managed named
volume, so the host-side `cp -a` / `rm -rf` workspace prep that runs as the ordinary operator
account never meets root-owned content. T015 supplies a supported Sandbox CLI/MCP read-only
plan plus explicit confirmation, targeting only the validated
`sandbox-nodestore-<family>` volume; raw Docker removal and broad prune remain prohibited.
Switching the service to `--user $(id -u)` was rejected: the runtime command starts with
`corepack enable`, which writes into the image's global bin directory and fails as a
non-root user.

## Technical Context

**Language/Version**: Python 3.11+ (`sandbox/` package), POSIX `sh` for remote command strings

**Primary Dependencies**: Docker Compose v2, git, GNU coreutils on the remote host

**Storage**: remote host ext4 (`/dev/sda1`); Docker named volumes; `$SANDBOX_HOME/runtime`

**Testing**: `unittest` under `tests/`, with a real-filesystem regression test that runs the
generated shell and exercises git

**Target Platform**: Linux remote host (`scaleway-sandbox`), macOS for local test execution

**Project Type**: CLI + MCP tooling (single Python package, single `sb` entry file)

**Performance Goals**: workspace materialization stays within the existing 120 s budget

**Constraints**: ext4 has no `FICLONE`, so copy-on-write clones are unavailable; `do_linkat`
returns `EXDEV` across *mounts*, not merely across filesystems; containers run as root;
no push-back path exists from the remote

**Scale/Scope**: ~180 deploy-src directories, ~90 MiB `.git` each; one project family
(`lenzora`) currently drives the package-store growth

## Constitution Check

| Principle | Assessment |
|---|---|
| I. Per-project is the only instance model | Unchanged. No new global instance; the shared volume is family-scoped, derived from the per-project runtime id. |
| II. The registry is the single source of truth | Unchanged. No new state file; the family is derived from the registry-held runtime id at overlay time. |
| III. Single entry file, modular package | New logic lands in `sandbox/workspaces/checkout.py` (new module) and existing `sandbox/runtimes/compose.py`; `sb` untouched. |
| IV. Live-stack verification is the only proof of done | Measurement is a real workspace materialization on the live remote with `df` deltas before/after, plus `git fsck` on the source. |
| V. Idempotency and docs-with-code | Both copy paths remain re-runnable; the refresh command still cleans before copying. `CLAUDE.md`, `README.md` (own subsection) and `docs/remote-hosting.md` land with the code. |
| VI. Feature parity before removal | Nothing is removed. The plain-copy path stays as the fallback whenever hard linking is unavailable, and non-opted-in Compose projects get a byte-identical overlay. |

**Gate result**: PASS, no deviations to justify.

Additional constraints honoured: no secret is echoed; the BuildKit
`--mount=type=cache,id=pnpm-store` build cache is untouched. T015 is the only resource-surface
extension and remains exact-name, read-only-plan, confirmation-gated, and manifest-registered.

## Project Structure

### Documentation (this feature)

```text
specs/044-shared-node-store-and-git-dedup/
├── plan.md
├── spec.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── workspace-materialization.md
│   └── node-store-overlay.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── workspaces/
│   └── checkout.py            # NEW: shared materialization contract (shell + python)
├── transports/
│   └── remote_jobs.py         # workspace_refresh_command uses the new copy plan
├── runtimes/
│   └── compose.py             # overlay emits the family-scoped node store contract
├── config/
│   └── compose.py             # parses compose.nodeStore opt-in
├── resources/
│   └── node_store.py          # exact-family reclaim plan/apply service
├── commands/
│   └── resources.py           # registered CLI seam
└── application/
    └── workspace_service.py   # local reset copy reuses the python materializer

tests/
├── test_workspace_git_dedup.py     # NEW: real-filesystem hardlink safety regression
└── test_compose_node_store.py      # NEW: overlay contract + family scoping

docs/
└── remote-hosting.md          # migration path for existing workspaces
```

**Structure Decision**: keep both fixes inside the existing modular package. A new
`sandbox/workspaces/checkout.py` owns the single definition of "how a workspace checkout is
materialized" so the remote shell path and the local Python path cannot drift; the Compose
overlay change stays inside the runtime adapter that already owns overlay generation.

## Complexity Tracking

No constitution violations; table intentionally empty.
