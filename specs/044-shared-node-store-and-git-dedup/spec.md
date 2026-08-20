# Feature Specification: Shared node store and hardlinked git workspaces

**Feature Branch**: `044-shared-node-store-and-git-dedup`

**Created**: 2026-08-16

**Status**: Draft

**Input**: User description: "Stop the two structural causes of remote host disk growth: (1) every remote workspace mints a brand-new empty per-workspace node_modules docker volume and a project-local pnpm store on the host bind, because /workspace/node_modules is a separate mount so pnpm cannot hardlink into a shared store; (2) every remote workspace is a full cp -a byte copy including the whole .git history. Sandbox must supply a shared, family-scoped node store mount plus store-dir/node-modules environment contract to generic Compose projects, and must materialize workspace checkouts with hardlinked git object storage instead of byte-copying history, while keeping existing workspaces working and documenting the migration."

## Context

On 2026-08-16 the remote host `scaleway-sandbox` reached 97% full. A read-only audit
(`memory/plugin-behavior/scaleway-sandbox-deploy-src-space-2026-08-16.md`) attributed the
growth to two structural causes that reclamation tooling can only mop up, never stop:

- 19 per-workspace package stores totalling 44.5 GiB, 87 host `node_modules` trees totalling
  9.83 GiB, and 17 per-workspace container volumes of ~1.49 GB each (~25 GiB).
- 177 full repository history copies totalling 19.2 GiB, of which only ~4.6 GiB is unique.

This feature removes both causes. It does not change reclamation, retention, or reporting,
which are owned elsewhere.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A new workspace costs history-free bytes (Priority: P1)

An operator submits a remote job. Sandbox materializes a fresh workspace from the deployed
source. Today that workspace duplicates the whole repository history; the operator watches
free space drop by roughly one full repository per job even though the history is identical.
After this feature, materializing a workspace consumes only the bytes that actually differ,
and the deployed source repository remains byte-for-byte intact and healthy afterwards.

**Why this priority**: It is the smallest, most self-contained change, it applies to every
project on the host (not just package-manager projects), and it can be measured and proven
without touching any hosted site.

**Independent Test**: Create one workspace from a real deployed source on the remote host,
measure the host's used bytes before and after, and confirm the source repository passes an
integrity check and reports no modified tracked files afterwards.

**Acceptance Scenarios**:

1. **Given** a deployed source checkout with repository history, **When** a workspace is
   materialized from it, **Then** the host's used bytes grow by materially less than the size
   of the source repository history, and the workspace behaves as a complete, self-contained
   checkout.
2. **Given** a materialized workspace, **When** the deploy path resets the workspace to a
   commit, discards untracked files, and unpacks the uncommitted-change layer over it,
   **Then** the source checkout's history, working tree, and integrity are unchanged.
3. **Given** a materialized workspace, **When** the source checkout is deleted,
   **Then** the workspace still resolves its own project root, derives the same instance
   name, and can read its own history.

---

### User Story 2 - Repeated builds reuse one package store (Priority: P2)

A project family (one deployed source plus its workspaces) installs dependencies inside its
containers. Today each workspace writes its own package store onto the host bind and mounts
its own empty dependency volume, so the same packages are downloaded and stored once per
workspace. After this feature, all workspaces of one family share a single store, dependency
trees are links into that store rather than copies, and neither the store nor the dependency
tree lands on the host bind.

**Why this priority**: It is the larger saving but needs a matching change in the project
being hosted, so it cannot ship as a Sandbox-only change.

**Independent Test**: Inspect the container configuration Sandbox produces for a
workspace-backed project and confirm one shared, family-scoped store mount, a dependency-tree
location inside that same mount, and no per-workspace store or dependency volume; then
confirm two different workspaces of the same family resolve to the same store.

**Acceptance Scenarios**:

1. **Given** two workspaces of the same project family, **When** each starts its container,
   **Then** both mount the same shared store and neither mints a new empty dependency volume.
2. **Given** a project that has not opted in, **When** its container starts, **Then** its
   configuration is unchanged from today.
3. **Given** a shared store, **When** the host prepares or removes a workspace directory as
   the ordinary (non-root) operator account, **Then** no permission failure occurs, because
   no store or dependency content is written into the host-visible workspace directory.

---

### User Story 3 - Existing workspaces keep working (Priority: P3)

The host already holds workspaces created the old way, plus their stores and volumes. After
upgrading Sandbox, those workspaces continue to run, and an operator has a documented,
reversible path to move them onto the new layout at their own pace.

**Why this priority**: Required for safe rollout on a live host, but delivers no bytes by
itself.

**Independent Test**: Point the new code at an existing old-layout workspace and confirm it
still starts, resets, and reports status; follow the documented migration steps and confirm
the same workspace then uses the shared layout.

**Acceptance Scenarios**:

1. **Given** a workspace created before this feature, **When** any workspace operation runs,
   **Then** it succeeds without requiring migration.
2. **Given** the documented migration steps, **When** an operator follows them,
   **Then** the reclaimed bytes are attributable and the affected project still builds.

---

### Edge Cases

- The source checkout has no repository history at all (never initialized): materialization
  still succeeds and produces the same tree as today.
- The source's history marker is a file pointing elsewhere (linked worktree or submodule)
  rather than a directory: materialization falls back to a plain copy rather than attempting
  to share storage.
- Storage sharing is impossible on the target filesystem (cross-device, unsupported): the
  operation falls back to a plain copy and still succeeds.
- A workspace rewrites or garbage-collects its own history: the source checkout must remain
  complete and healthy.
- A workspace and the source are refreshed concurrently: neither may observe a partially
  written history.
- The shared store is deleted while a container is running: the next install repopulates it;
  nothing else breaks.
- Two workspaces of the same family install different dependency versions at the same time:
  both succeed and neither corrupts the other's dependency tree.
- A project opts in but its container image cannot use the supplied locations: the container
  still starts and installs, at the old cost, without failing.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Workspace materialization MUST share the source checkout's immutable history
  storage with the new workspace instead of duplicating it, whenever the platform allows it.
- **FR-002**: Workspace materialization MUST NOT share any mutable repository state (current
  branch pointer, index, reference logs, configuration, editor scratch files); every mutable
  file MUST be a private copy in the workspace.
- **FR-003**: Workspace materialization MUST fall back to a full copy, and still succeed, when
  storage sharing is unavailable or the history marker is not a directory.
- **FR-004**: After any workspace-side repository operation performed by the deploy, test, or
  build paths, the source checkout MUST remain unmodified and pass an integrity check.
- **FR-005**: A materialized workspace MUST remain self-contained: deleting the source
  checkout MUST NOT break the workspace's root discovery, derived instance name, build stamp,
  or recovery capture.
- **FR-006**: The automated test suite MUST include a test that fails if any of the deploy,
  reset, discard, unpack, test, or build steps writes in place into storage shared with the
  source checkout.
- **FR-007**: For projects that opt in, Sandbox MUST provide the container with exactly one
  shared package-store location that is scoped to the project family, not to the individual
  workspace.
- **FR-008**: The family scope MUST be derived so that a source checkout and every workspace
  materialized from it resolve to the same shared store, while different projects never share
  one.
- **FR-009**: Sandbox MUST provide the container with a dependency-tree location that lives
  inside the same mount as the shared store, so that link-based installs are possible.
- **FR-010**: The shared store and the dependency tree MUST NOT be written into the
  host-visible workspace directory, so that host-side preparation and deletion performed by
  the ordinary operator account never encounters content it cannot remove.
- **FR-011**: Reclaiming a shared store MUST be possible through an ordinary, named,
  non-destructive operation that does not require deleting unrelated data.
- **FR-012**: Projects that do not opt in MUST see byte-identical container configuration to
  today.
- **FR-013**: Sandbox MUST NOT alter the existing build-time package cache, which is already
  shared and correct.
- **FR-014**: Workspaces created before this feature MUST keep working without migration, and
  the migration path MUST be documented alongside the code.
- **FR-015**: The opt-in MUST be an explicit, reviewable declaration in the project's own
  configuration; Sandbox MUST NOT infer it from package files or run project scripts.

### Key Entities

- **Source checkout**: the deployed, per-project directory on the remote host that receives
  deployments and owns real repository history.
- **Workspace**: a job-scoped materialization of a source checkout; needs a working tree and a
  present history marker, but no independent history.
- **Project family**: a source checkout together with every workspace materialized from it;
  the unit that shares one package store.
- **Shared store**: the single, family-scoped location holding package content that dependency
  trees link into.
- **Dependency tree**: the per-workspace directory of installed dependencies, made of links
  into the shared store.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Materializing a workspace from a source checkout whose history is ~89 MiB
  consumes at least 80% fewer additional host bytes than today, measured as used-space delta
  on a real host, not estimated.
- **SC-002**: After the measured workspace goes through deploy reset, discard, and
  uncommitted-layer unpack, the source checkout reports zero modified tracked files and passes
  an integrity check.
- **SC-003**: The second and every later workspace of one project family adds no new package
  store and no new dependency volume; the family's store count stays at one regardless of
  workspace count.
- **SC-004**: No workspace operation performed by the ordinary operator account fails on
  permissions because of content written by a container.
- **SC-005**: Every existing workspace on the host continues to start, reset, and report
  status after the upgrade, with no migration step required first.
- **SC-006**: The regression suite fails if shared history storage is ever mutated in place by
  the deploy, test, or build paths.

## Assumptions

- The remote host's filesystem supports hard links within one filesystem but not copy-on-write
  clones; clone-based copying is therefore out of scope.
- Containers continue to run as their image's default account; the design must not depend on
  changing that account.
- Only the project being hosted can decide where its dependency tree lives, so the opt-in
  requires a matching change in that project's own container definition; Sandbox supplies the
  locations and the shared mount.
- A workspace never needs to publish history back anywhere; there is no push-back path from
  the remote host.
- Reclamation, retention, scheduling, and reporting of storage are owned by separate work and
  are out of scope here.
- Measurement is performed by creating exactly one temporary workspace on the healthy remote
  host and removing exactly what was created.
