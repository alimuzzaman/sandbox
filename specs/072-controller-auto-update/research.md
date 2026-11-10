# Research: Controller Auto-Update

## R1. Where the remote's installed source revision comes from

- **Decision**: (a) `migrate_remote_mcp_service` records the 40-hex
  `source_revision` it installed in the local hint store after a successful
  confirmed install; (b) the same install command writes
  `$HOME/sandbox/runtime/source-revision.json` on the remote after the unit is
  active; (c) `remote_mcp_service_status` reads that receipt inside its
  existing ssh command and records it.
- **Rationale**: the status probe returns only the 24-hex content digest
  (`installed_runtime_revision`), the registration's `mcp_service` record keeps
  only that digest, and the stage `receipt.json` is deleted on success
  (`sandbox/core/_remote.py` install command). All checkouts on a machine share
  `$SANDBOX_HOME`, so (a) alone covers the owner's case: the `latest` worktree
  migrates, the installed checkout sees it on its next remote command. (b)+(c)
  cover remotes migrated from other machines without a new round trip.
- **Alternatives**: a `SANDBOX_REMOTE_MCP_SOURCE_REVISION` unit line (changes the
  exact-key record and the unit renderer, more surface); parsing
  `runtime/helpers/image-stage/*-revision-<sha>` (several bundles coexist, so it
  does not identify the installed one); a new `git ls-remote`-style call per
  command (extra round trip, forbidden).

## R2. When to update

- **Decision**: at the start of a remote-capable command, right after argument
  parsing, before `maybe_auto_migrate` or any handler. Revisions learned during
  a command take effect at the next one.
- **Rationale**: updating after a command has acted and then re-running it would
  run its effects twice. Before dispatch nothing has happened yet.
- **Alternatives**: re-exec at the readiness gate (local effects may already
  have run; held locks and temp files); update at command end without re-exec
  (contradicts the approved "re-exec the original command once").

## R3. Remote-capable commands

- **Decision**: `remote`, `deploy`, `host`, `preview`, `hermes`; any command
  whose parsed args carry a non-empty `remote`; and `test`, `e2e`, `ci`, `job`,
  `jobs` unless `--local` was given (they may route to the project's declared
  remote). Hints from every remote count.
- **Rationale**: no project config is loaded before dispatch, so the declared
  remote cannot be resolved cheaply. Trust comes from origin, not from which
  remote reported the revision, so counting all remotes is safe.

## R4. Trust and movement

- **Decision**: clean `GIT_*` environment; `GIT_TERMINAL_PROMPT=0`; stdin
  `/dev/null`. Read upstream via `branch.<b>.remote` and `branch.<b>.merge`;
  require remote `origin` whose configured URL (`git config --get
  remote.origin.url`, raw, without `insteadOf`) is a GitHub URL
  (`https://github.com/`, `git@github.com:`, `ssh://git@github.com/`). Fetch
  `git fetch --no-tags --quiet origin +refs/heads/<b>:refs/remotes/origin/<b>`.
  Then require `hint != HEAD`, `merge-base --is-ancestor HEAD hint` and
  `merge-base --is-ancestor hint origin/<b>`; move with
  `git merge --ff-only --quiet <hint>`.
- **Rationale**: containment in the just-fetched upstream proves the commit is
  published history; ancestry from `HEAD` proves fast-forward; merging the exact
  hint never moves past it.
- **Test seam**: tests set the raw origin URL to
  `https://github.com/example/sandbox.git` and map it to a local bare repo with
  `url.<file-url>.insteadOf`, so no code-level injection is needed. That mapping
  is the local user's own git config, which is already trusted.

## R5. Cleanliness

- **Decision**: `git status --porcelain --untracked-files=no` must be empty.
  Untracked files do not block; if the fast-forward would overwrite one, git
  refuses and the controller prints the failure notice.
- **Rationale**: matches "working tree and index are clean" without blocking on
  stray scratch files; git itself protects untracked content.

## R6. Lock

- **Decision**: `fcntl.flock(LOCK_EX | LOCK_NB)` on
  `$SANDBOX_HOME/runtime/locks/controller-update-<sha256(root)[:16]>.lock`, with
  up to 2 s of polling. After acquiring, re-read `HEAD` and cleanliness.
- **Rationale**: the kernel releases `flock` when the process dies, so a crash
  never leaves a stale lock. Re-reading under the lock closes the race with a
  concurrent updater. If another process already moved `HEAD` to the hint, this
  process re-executes too, because its loaded modules are the old code.

## R7. Refresh and rollback

- **Decision**: after a fast-forward, if `.cli-venv/bin/python` exists, run
  `.cli-venv/bin/python -m sandbox.controller_update.refresh` with `cwd=ROOT`,
  which calls the new code's `_ensure_cli_requirements` (a no-op when the
  requirements digest stamp matches). If `mcp/**/requirements*.txt` changed,
  print a notice to run `./sb mcp-install`. On refresh failure, roll back with
  `git read-tree -m -u <new> <old>` then
  `git update-ref refs/heads/<b> <old> <new>` (compare-and-swap).
- **Rationale**: reuses the existing venv convergence path and its digest stamp;
  the refresh runs the new revision's code. The two-step rollback refuses to
  overwrite local edits and refuses if the branch moved meanwhile.
- **Alternatives**: `git reset --keep` (equivalent, but the task forbids reset
  verbs); full `./sb setup` (Docker preflight, prompts, too broad).

## R8. Re-exec

- **Decision**: `os.execve(ENTRY, [str(ENTRY), *sys.argv[1:]], env)` with
  `SANDBOX_AUTO_UPDATE_REEXEC=1` added. `ENTRY` is the resolved checkout `sb`,
  so the shell bootstrap picks the interpreter again under the new code.
- **Rationale**: stdin has not been read, no locks are held (released before
  exec), and Python fds are non-inheritable by default.

## R9. Machine output, job children, opt-out

- **Decision**: skip silently when `getattr(args, "json", False)` is true, when
  `SANDBOX_DURABLE_JOB_ID` is set, when `SANDBOX_AUTO_UPDATE_REEXEC` is set, or
  when the process is not the real `sb` entry. `SANDBOX_AUTO_UPDATE` accepts
  `0/false/no/off` and `1/true/yes/on`; otherwise
  `defaults.controller_auto_update` from the merged machine config decides
  (default on). The config is read only when an update would otherwise start.
- **Rationale**: silent machine runs keep both streams exact for agents and the
  MCP server; reading config late keeps the common path cheap.

## R10. Refusal memo

- **Decision**: a hint refused after a fetch (not on upstream, not a
  descendant) is stored with its upstream and an expiry 6 h ahead; within that
  window it is skipped silently with no fetch. A failed fetch is memoized for 15 minutes so an offline
  controller pays the fetch timeout at most once per window.
