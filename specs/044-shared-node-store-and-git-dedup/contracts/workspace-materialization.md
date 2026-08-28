# Contract: workspace materialization and Git history sharing

This contract is the normative boundary for the locally implemented
`sandbox/workspaces/checkout.py` helper. The existing remote seam
`sandbox/transports/remote_jobs.py::workspace_refresh_command(source_path,
workspace_path)` remains the command-producing entry point and must delegate to the same
copy plan used by the local `WorkspaceService` reset path. This file describes intended
behaviour. Local implementation does not satisfy the remote evidence gate.

## Inputs and outputs

### Python seam

The implementation exposes one pure-planning/one-I/O split so both callers use the
same rules:

```python
plan = plan_materialization(source: Path, workspace: Path) -> MaterializationPlan
receipt = materialize(plan) -> CheckoutMaterializationReceipt
```

The public compatibility wrapper may remain
`workspace_refresh_command(source_path: str, workspace_path: str) -> str` and must render a
quoted POSIX shell command that invokes the same plan. The local lifecycle adapter calls
`materialize` rather than `shutil.copytree`; no caller may implement a second copy algorithm.

The request is rejected before mutation when either path is missing, non-absolute, a symlink,
the same resolved path, outside the caller's deployment boundary, or not a directory where a
directory is required. A workspace label is validated by the existing
`[A-Za-z0-9][A-Za-z0-9_.-]{0,63}` grammar before a path is built. The helper never accepts a
caller-provided shell fragment, package command, or arbitrary destination root.
Planning binds the parent, source, and existing workspace to their no-follow device/inode
identities. Materialization opens the parent/source with `O_NOFOLLOW`, rechecks those exact
identities, and performs workspace publication and deletion relative to the opened parent and
workspace descriptors. A path replacement after planning is a bounded refusal; it never
authorizes following or deleting through the replacement.

The result has the `CheckoutMaterializationReceipt` shape in `../data-model.md`. It reports
`history_mode=hardlinked`, `copied`, or `none`, the count of linked/copy entries, and a
bounded fallback reason. It does not report host-space savings or an integrity pass unless a
separate evidence seam supplied those observations.

## Materialization algorithm

The following ordering is required and is idempotent for the same source snapshot:

1. Resolve and validate source/workspace ownership and acquire the per-source lock. The lock
   is outside either `.git` tree and is held through source snapshot, staging, publication,
   and cleanup. Parent/source descriptor acquisition and lock creation are inside exhaustive
   cleanup: every opened descriptor is closed exactly once on failure and raw OS details are
   replaced by a bounded error. If held by another operation, return
   `workspace_materialization_busy` without deleting the existing workspace.
2. Create a sibling staging directory under the workspace's authorized parent. Do not stage
   inside `source/.git`, and do not use a host-global temporary directory that could cross a
   filesystem boundary accidentally.
3. Copy worktree entries by value, preserving symlinks as symlinks and preserving the current
   top-level directory inode policy used by `workspace_refresh_command`. Existing nested
   Compose bind-mount directories are cleaned in place; the workspace root is not replaced by
   a broad `rm -rf`.
4. Classify `source/.git`:

   - **Absent**: finish the worktree copy with `history_mode=none`.
   - **Directory**: copy all Git metadata by value except the eligible immutable objects and
     packs. Create a private staging `objects` directory, then hard-link each regular loose
     object and regular file under `objects/pack/` from source. Copy `objects/info/` by value.
     Never link a symlink, special file, marker, config, index, ref, reflog, hook, or
     worktree-admin entry.
   - **Regular marker file**: parse a bounded `gitdir: <path>` value. When it resolves to a
     readable administrative directory, copy it by value to a workspace-private path and
     rewrite the workspace marker to that path. Do not hard-link any object. Malformed or
     unreadable markers are rejected with `workspace_git_marker_invalid`; their source pointer
     is never copied unchanged into staging or the workspace.
   - **Other type**: select the plain-copy fallback with a bounded reason.

   A symlink at `.git` or anywhere inside copied Git administration is rejected. Git metadata
   symlinks are never preserved into the workspace, including marker-clone administration.

5. Publish the staged tree transactionally after all files are present. Existing workspace
   entries move into a sibling backup before staged entries are installed; directories needed
   by bind mounts retain their inode. Any failure during backup or installation restores both
   already-moved and not-yet-moved prior entries. The old workspace is never deleted merely
   because publication failed.
   If any link operation returns `EXDEV`, `EOPNOTSUPP`, `ENOTSUP`, `EINVAL`, `ENOSYS`,
   `EPERM`, or `EACCES`, remove only the unreferenced staging tree and copy the complete Git
   metadata by value. A fallback never leaves a mixture of source-linked and copied object
   files. If the plain copy fails, retain the prior workspace and report a bounded error.
6. Release the lock before returning the receipt. A successful receipt reports
   `released=true` only after exact lock removal and descriptor cleanup succeed; release failure
   is a bounded failure, never a successful receipt. A later refresh may replace the workspace
   contents, but it must acquire the same lock and repeat the atomic publication.

The source checkout is read-only from this algorithm's point of view. It must not run `git
reset`, `git clean`, `git gc`, `chmod`, `unlink`, or any write command in the source directory.

## Mutable-state boundary

The workspace gets private copies of all mutable Git state, including:

```text
.git/HEAD
.git/index
.git/refs/
.git/logs/
.git/packed-refs
.git/config
.git/config.worktree (when present)
.git/description
.git/hooks/
.git/info/exclude
.git/COMMIT_EDITMSG (when present)
.git/worktrees/ and other linked-worktree administrative files
```

Only regular immutable object/pack files may share an inode with source. Git operations in the
workspace are allowed to create new objects, unlink old object files, update private refs and
index files, and rewrite private configuration. The implementation must never expose source
object paths through `objects/info/alternates`, `core.worktree`, `gitdir`, or generated build
stamps.

## Atomicity and concurrency

- A reader sees either the previous complete workspace or the next complete workspace; it
  never sees a partially copied `.git/objects` tree.
- Source deployment/reset and workspace materialization use one internally derived source
  lock key; public planning accepts no caller lock override. The remote deploy path uses
  `update_target_to` to hold that exact lock continuously across reset, dirty-overlay archive
  transfer, and publication. Separate reset/apply lock windows are not an accepted deploy path.
- Remote workspace preparation supplies the staged Sandbox runtime directory as an exact
  `PYTHONPATH` before invoking the shared module.
- Two distinct workspace labels may materialize concurrently only when their source lock
  implementation can prove that both read the same immutable source snapshot. Otherwise one
  receives `workspace_materialization_busy` and the caller retries after the first completes.
- A failed refresh leaves the prior workspace usable and leaves source bytes, tracked status,
  refs, and object inodes unchanged.

## Legacy and rollback behaviour

1. An existing old-layout workspace is never forced through migration. `start`, `status`,
   `reset`, and `build` continue through the legacy copy path when no shared-history receipt
   exists.
2. A reset of a receipt-backed new workspace uses this contract and the same source lock. It
   does not call `shutil.copytree` or reintroduce an independent object copy.
3. If a hard-link or marker-file fallback is selected, the workspace remains a valid private
   checkout and reports `history_mode=copied`; the operator may continue at the old storage
   cost or retry after changing the filesystem layout.
4. Rollback is a planned, explicit operation: switch the caller to the plain-copy mode,
   preserve the old workspace and receipts, and verify the source before any named reclaim.
   There is no automatic deletion of old workspaces, object stores, or Docker volumes.

## Required local test seams

`tests/test_workspace_git_dedup.py` must exercise the following seams without a remote host:

| Seam | Falsifiable assertion |
|---|---|
| Real Git fixture with loose objects and packs | Worktree contents match source; eligible object/pack files share inode; `HEAD`, index, refs, logs, config, and marker do not. |
| Workspace `git reset`, branch/ref update, untracked discard, and a benign object write | Source `git status --porcelain`, tracked-file hashes, refs, config, and `git fsck --full` are unchanged. |
| Injected `os.link`/linkat `EXDEV` or unsupported error | Receipt says `history_mode=copied`, no staged source links remain, workspace still reads history, source is unchanged. |
| `.git` marker file pointing to a linked worktree | Marker is copied by value and rewritten to a private path; deleting source does not break workspace root discovery or `git rev-parse`. |
| Missing `.git` | Materialization succeeds with `history_mode=none`. |
| Concurrent refresh/lock seam | Second call returns `workspace_materialization_busy`; it does not clean the first workspace. |
| Remote command renderer | `workspace_refresh_command` contains no unquoted source mutation, uses the same staging/fallback rules, retains top-level bind-mount inodes, and has no raw caller shell text. |
| Local reset seam | `WorkspaceService._local_lifecycle("reset", ...)` calls the shared materializer; no `shutil.copytree` path remains for receipt-backed workspaces. |
| Path replacement and publication fault seams | A planned-absent workspace replaced by a symlink cannot touch its target; source/workspace inode replacement refuses; failures during partial backup or staged publication restore all prior bytes and the workspace inode. |
| Git metadata symlinks | A symlinked `.git` or any symlink below Git administration is refused and never appears in the workspace. |
| Deploy mutation locking | Remote reset and uncommitted-overlay apply acquire the exact same source lock name as materialization for their full mutation boundary. |

The exact source integrity assertion must snapshot bytes and metadata before the operation,
then run `git status --porcelain=v1`, `git diff --exit-code`, and `git fsck --full` on the
source after every deploy/reset/discard/unpack/test/build step. A test that only compares
workspace contents or only checks an estimated byte delta is insufficient.

## Required live-remote evidence (future release gate)

On the configured remote, with no secrets or unrelated cleanup:

1. Deploy one real source checkout and record `df`/used-byte measurements before and after
   exactly one temporary workspace materialization. Preserve the command, source revision,
   workspace label, receipt, and measured delta.
2. Run the deploy reset, `git clean -fd` discard, dirty-layer unpack, declared test, and
   declared build paths. Capture source `git status --porcelain`, `git diff --exit-code`,
   `git fsck --full`, and the object/link evidence after each boundary.
3. Delete only the temporary workspace through its supported lifecycle path and verify the
   source checkout remains byte-for-byte/integrity clean. Do not remove a source checkout or
   use broad host cleanup.

Remote evidence is an implementation/release gate, not evidence supplied by this planning
artifact. Deployments, DNS/ACME, secret inspection, and production changes are out of scope.
