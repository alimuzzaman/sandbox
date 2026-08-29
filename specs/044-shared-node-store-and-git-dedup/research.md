# Research: Shared node store and hardlinked Git workspaces

This research records the design decisions used by the local Spec 044 implementation. It
contains no live-host evidence or release certification.

## R1 — What part of a Git checkout may be shared?

**Decision**: share only the immutable object files below `.git/objects` by hard link. Copy
the worktree and every other Git entry by value. The object copy is assembled in a private
temporary directory and published atomically; a partially linked tree is never exposed as a
workspace checkout.

**Rationale**: `sandbox/transports/remote_jobs.py::workspace_refresh_command` currently
copies `source/.` wholesale. Git's loose objects and pack files are content-addressed and
are replaced by creating a new file followed by an unlink when Git repacks or garbage
collects. `HEAD`, the index, refs, reflogs, config, hooks, and worktree metadata are mutable
and must not share an inode with the source. Keeping the boundary at regular object and
pack files makes the source checkout a protected immutable store while leaving all normal
workspace Git operations private.

**Alternatives considered**:

- A complete `cp -a` (rejected: it duplicates history and is the defect being fixed).
- Hard-linking the entire `.git` directory (rejected: it would share `HEAD`, refs, reflogs,
  config, or index and would make a workspace operation mutate the source).
- Git alternates or a shared object database (rejected for this feature: it introduces a
  source-path dependency and can make a workspace unusable after source removal).
- Copy-on-write clone (rejected: the target ext4 host has no usable `FICLONE` path and the
  plan explicitly keeps clone-based copying out of scope).

## R2 — How are hard-link failures handled?

**Decision**: hard-linking is an optimisation, not a prerequisite. If the history marker is
not a directory, the object directory cannot be read, or any link operation fails with an
unsupported/cross-device/permission error (`EXDEV`, `EOPNOTSUPP`, `ENOTSUP`, `EINVAL`,
`ENOSYS`, `EPERM`, or `EACCES`), discard the private staging tree and copy the complete Git
metadata by value. The worktree remains a normal copy in both modes. The receipt records
`history_mode=hardlinked` or `history_mode=copied` and the reason for a fallback; callers do
not infer success from a byte estimate.

**Rationale**: a remote job must still start on filesystems that do not support links or
when the source and target cross a device boundary. Removing the staging tree before the
plain-copy retry prevents a mixed mode in which some source objects are accidentally shared
and others are private. The source checkout is never modified during either path.

**Alternatives considered**: fail the job on `EXDEV` (rejected: FR-003 requires a successful
plain-copy fallback); silently continue after a partial link failure (rejected: it makes the
mutability boundary unknowable); copy only loose objects and omit packs (rejected: the
workspace must remain a complete, self-contained checkout).

## R3 — How is mutable Git state kept private?

**Decision**: copy by value, at minimum, `HEAD`, `index`, `refs/`, `logs/`, `packed-refs`,
`config`, `config.worktree`, `description`, `hooks/`, `info/exclude`, `COMMIT_EDITMSG`, and
the worktree administrative files. Only regular loose-object files and regular pack files
under `objects/pack/` are eligible for hard links. `objects/info/` and any symlink, socket,
device, or unexpected object entry is copied by value or causes the conservative plain-copy
fallback; it is never hard-linked as mutable metadata.

**Rationale**: Git normally treats object and pack contents as immutable, but it does update
the index, refs, reflogs, configuration, hooks, and housekeeping metadata. Explicitly
listing the private set gives the regression test a finite inode comparison instead of a
vague promise that “`.git` is private”.

**Linked-worktree/submodule marker**: when `.git` is a regular marker file rather than a
directory, the materializer does not attempt object sharing. It resolves a valid `gitdir:`
target, copies that administrative directory by value into a workspace-private location,
and rewrites the workspace marker to that private location. If the marker is malformed or
cannot be resolved, it uses the complete plain-copy compatibility path and returns a bounded
fallback reason. No workspace marker may continue to point into the source checkout after a
successful materialization.

## R4 — How are source refreshes and workspace refreshes made race-safe?

**Decision**: both the remote shell command and local Python materializer use the same
per-source materialization lock and publish the staged `.git` directory with an atomic
rename. A refresh cleans and repopulates the workspace only while holding the lock; an
existing top-level directory inode used by a Compose bind mount remains in place, as today.
The lock is outside `.git/objects` and is never hard-linked. A second refresh fails with a
bounded `workspace_materialization_busy` result rather than observing a half-written history.

**Rationale**: `prepare_remote_workspace` and `RemoteJobTransport._prepare_workspace` can
be called by separate durable jobs, while `WorkspaceService._local_lifecycle` can reset a
local receipt-backed checkout. Atomic publication protects readers from a partially copied
object directory; the lock prevents a source reset/deploy and a workspace refresh from
crossing the source snapshot boundary.

**Alternatives considered**: rely on directory listing order (rejected: it does not prevent
  a concurrent source reset); lock only the workspace (rejected: it does not coordinate
  source deploy); remove and recreate the whole workspace (rejected: existing bind-mount
  inodes and the current cleanup contract must remain stable).

## R5 — What is the node-store opt-in and family identity?

**Decision**: `compose.nodeStore` is a strict boolean in the project-owned Compose
descriptor. Missing or `false` means legacy behaviour; `true` is the only opt-in. The
normalizer rejects strings, numbers, arrays, and objects rather than guessing. Sandbox does
not inspect package files or run project scripts to infer the choice.

For an opted-in runtime, the family key is derived from the canonical runtime identifier.
For a workspace runtime whose root name contains the deterministic
`-workspace-<14-lowercase-hex>` segment, remove that segment before forming the family key;
the source runtime keeps its source identity and any collision suffix. A malformed or
ambiguous marker is not stripped. The resulting family key is lower-case and restricted to
the existing runtime/Compose identifier grammar, so two canonical project identities cannot
silently select the same volume. The derivation helper is pure and is tested with source,
workspace, collision, and different-project cases.

**Rationale**: the current `ComposeSchemaProvider` already owns project descriptor
normalization, and `ComposeAdapter._runtime_id` already supplies a bounded per-project
identifier. Keeping the declaration in `sandbox.config.*` makes the migration reviewable;
keeping family derivation in the Compose adapter means the registry remains the source of
truth and no new state file is needed.

**Alternatives considered**:

- Infer from `package.json`, lockfiles, or package-manager commands (rejected by FR-015).
- Use the workspace label (rejected: labels intentionally identify independent jobs).
- Use one global node store (rejected: it would cross project-family ownership boundaries).
- Put the store under the host-visible workspace bind (rejected: root-owned container files
  would make ordinary operator cleanup fail).

## R6 — What exactly does the overlay provide?

**Decision**: for `nodeStore=true`, the generic Compose overlay adds one Docker-managed named
volume, mounted read-write at `/sandbox-node`, and exports exactly these locations to the
declared service:

```text
SANDBOX_NODE_STORE=/sandbox-node/store
SANDBOX_NODE_MODULES=/sandbox-node/node_modules/<canonical-runtime-id>
npm_config_store_dir=/sandbox-node/store
```

The named volume is `sandbox-nodestore-<family>` (with an explicit Compose `name:` so the
engine does not prepend the per-workspace Compose project). The project-owned Compose file
must point its dependency tree at `$SANDBOX_NODE_MODULES` and must remove its old
per-workspace `node_modules` volume. Sandbox never rewrites the project command or guesses a
package manager. If the image ignores these variables, the service still starts and uses its
old layout; the overlay does not fail merely because an image cannot consume the contract.

**Rationale**: keeping the store and dependency tree in one Docker volume allows pnpm's
link-based install to stay on one filesystem and avoids root-owned files in the host bind.
The three names are intentionally small, explicit, and usable by npm-compatible tooling.

**Alternatives considered**: separate store and dependency volumes (rejected: links can
  return `EXDEV`); a host bind mount (rejected: uid/permission failures); changing the
  container user to the operator (rejected: the existing `corepack enable` startup path
  requires the image's root account); mutating project Compose YAML from Sandbox (rejected:
  project configuration remains the project owner's responsibility).

## R7 — What remains unchanged for legacy projects?

**Decision**: absent/false opt-in emits the exact current overlay bytes: ports, CPU, memory,
and PID limits only, with no node-store volume, environment variables, or reclaim command.
Existing workspaces continue to use their old bind and anonymous/per-workspace dependency
volumes. The new materializer retains the plain-copy branch, so a pre-feature workspace can
be reset or started without first migrating. Migration is opt-in and reversible: stop the
affected service, capture a named plan, update the project declaration and Compose mount,
recreate only the named family volume, and retain the old workspace layout until a measured
cutover is accepted. No automatic deletion or broad `docker volume prune` is permitted.

**Rationale**: Feature-parity-before-removal (Constitution VI) and FR-014 require old
workspaces to remain usable. Retaining the fallback also gives operators a rollback path if a
project image cannot consume the supplied locations.

## R8 — What evidence is required before claiming success?

**Decision**: local unit/static checks are necessary but not sufficient. Before implementation
is called complete, the required evidence is:

1. A real remote workspace materialization from a deployed source with a recorded used-space
   delta, hard-link/fallback mode, and source `git status --porcelain` plus `git fsck` result.
2. The same workspace after deploy reset, untracked discard, dirty-layer unpack, test, and
   build seams, with the source inode/content snapshot unchanged.
3. Two real workspaces of one opted-in family and one non-opted-in project, showing one exact
   named volume for the family, no per-workspace node-store volume, and unchanged legacy
   overlay bytes for the control project.
4. A permission probe that prepares/removes the host-visible workspace as the ordinary
   operator account while a container uses the shared volume.
5. A documented migration plan and a dry-run/explicit-confirmation named reclaim check; no
   automatic reclaim, broad prune, deployment, DNS, or secret access is part of this feature.

No remote host measurement, performance result, implementation test result, or release claim
is asserted by this planning package.
