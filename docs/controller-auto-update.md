# Controller auto-update

Spec: [`specs/072-controller-auto-update`](../specs/072-controller-auto-update/spec.md).

A controller checkout (the repository your `sb` lives in) catches up by
itself when a remote it talks to runs a newer **published** Sandbox. The
remote only nominates a revision. The checkout's own GitHub `origin` decides
whether that revision is on the branch the checkout follows, and the
controller only ever fast-forwards to it.

## When it runs

At the start of a remote-capable command, right after argument parsing and
before the command does anything:

- `sb remote ...`, `sb deploy`, `sb host`, `sb preview`, `sb hermes`;
- any command given `--remote NAME` (`wp`, `ensure`, `test`, `e2e`, `ci`, ...);
- `sb test`, `sb e2e`, `sb ci`, `sb job`, `sb jobs` unless run with `--local`.

Other commands never check.

## Where the remote revision comes from

No extra round trip is added. Revisions are recorded in
`$SANDBOX_HOME/runtime/controller-update/state.json`, which every checkout on
the machine shares:

1. A successful `sb remote service migrate NAME --confirm` (and provision or
   `remote up`, which use the same installer) records the git revision it
   installed. A migrate run from one checkout, such as the `latest` worktree,
   is visible to the installed `sb` on its next remote command.
2. The same install leaves `$HOME/sandbox/runtime/source-revision.json` on the
   remote (`{"schema_version":1,"source_revision":"<40-hex>"}`, mode 0600).
   `remote_mcp_service_status`, which readiness checks and
   `sb remote service status` already run, reads it inside its existing ssh
   call, reports it as `installed_source_revision`, and records it. Remotes
   installed before this feature have no receipt until their next migrate.

A revision learned during a command takes effect at the start of the next
remote-capable command. A command never updates part way, so no effect runs
twice.

## Trust rule

The controller moves only when all of these hold:

- the recorded revision is a full 40-hex commit id that differs from `HEAD`;
- the checkout is on a branch (not detached) whose upstream is on the remote
  named `origin`;
- `origin`'s configured URL is a GitHub URL (`https://github.com/...`,
  `git@github.com:...`, `ssh://git@github.com/...`);
- tracked files and the index are clean (untracked files do not block, but git
  refuses a fast-forward that would overwrite one);
- no other Sandbox process holds the update lock
  (`$SANDBOX_HOME/runtime/locks/controller-update-<digest>.lock`, `flock`,
  released automatically when a process exits);
- after `git fetch --no-tags origin <upstream>` the revision is an ancestor of
  `origin/<upstream>` and a descendant of `HEAD`.

It then runs `git merge --ff-only <revision>`: exactly that revision, never
past it. The server is never fetched from.

When several remotes have recorded revisions, the newest one that passes the
rule wins.

## After moving

1. `python -m sandbox.controller_update.refresh` runs from the updated
   checkout (with `.cli-venv/bin/python` when it exists). It converges
   `.cli-venv` through the existing digest-stamped installer, which is a no-op
   when `requirements-cli.txt` did not change, and import-checks the new CLI.
   Later revisions keep this entry point.
2. If MCP server requirements changed, one line asks you to run
   `./sb mcp-install` and restart your MCP client. The MCP server itself never
   self-updates.
3. The original command is re-run once, with the same interpreter, entry
   point, arguments and environment plus `SANDBOX_AUTO_UPDATE_REEXEC=1`, which
   forbids any further update in that process tree.

If the refresh fails, the checkout goes back to the previous revision
(`git read-tree -m -u` then a compare-and-swap `git update-ref`) and the
command runs on the old code. If even that fails, `sb` prints where the
checkout is and `git -C <root> status`, and exits 1 without running the
command.

## Notices

One line on stderr, prefixed `sb: `, for example:

```text
sb: controller updated 1a2b3c4 -> 5d6e7f8 from origin/latest; re-running the command.
sb: controller not updated: remote demo runs 5d6e7f8, but this checkout is pinned (detached at 1a2b3c4).
sb: controller not updated: remote demo runs 5d6e7f8, which is not on origin/latest.
```

Skip reasons: pinned (detached), no published upstream, origin not GitHub,
uncommitted changes, another update in progress, fetch failed, not on the
followed branch, not a fast-forward, fast-forward failed. The full table is in
[`contracts/cli-behavior.md`](../specs/072-controller-auto-update/contracts/cli-behavior.md).

To stay quiet:

- pinned, no-upstream and dirty notices print only when the newer revision is
  already known locally, so the claim is checked; they never fetch;
- a revision refused after a fetch is remembered for 6 hours, a failed fetch
  for 15 minutes, and neither fetches nor prints again inside that window. A
  remote migrated from an unpublished feature branch is refused once.

## Never updates

- **Pinned controllers**: detached checkouts such as
  `.scratch/sandbox/controller-<sha>` get the notice only.
- **Machine-readable runs**: with `--json` nothing happens and nothing is
  printed on either stream, so output is byte-identical to a run with the
  feature off. Agents and the MCP server keep exact output; a human run of the
  same command updates.
- **Durable job children** (`SANDBOX_DURABLE_JOB_ID` set) run the code their
  parent admitted.
- **The re-run** after an update (`SANDBOX_AUTO_UPDATE_REEXEC` set).
- **Non-git copies**, such as the staged runtime on a remote host: silent.
- Imports of the package from tests or other tools (only the real `sb` entry
  checks).

## Turning it off

| Control | Values | Notes |
|---|---|---|
| `SANDBOX_AUTO_UPDATE` | `0`, `false`, `no`, `off` / `1`, `true`, `yes`, `on` | Wins over the config key. |
| `defaults.controller_auto_update` | YAML bool, default `true` | In `$SANDBOX_HOME/sandbox.local.yml` (machine) or `sandbox.yml`. Read only when an update would otherwise start. |

```yaml
# $SANDBOX_HOME/sandbox.local.yml
defaults:
  controller_auto_update: false
```

## Follow latest (one-time)

The installed `sb` should follow `latest`. Because `latest` is already
checked out in another worktree, the installed checkout uses its own local
branch, `installed`, whose upstream is `origin/latest`. Run from the installed
checkout (the directory `~/.local/bin/sb` points into):

```sh
cd "$(dirname "$(readlink -f ~/.local/bin/sb)")"
git status --short                 # must print nothing
git remote get-url origin          # must be a github.com URL
git fetch --no-tags origin latest
git switch -c installed --track origin/latest
git rev-parse --abbrev-ref @{upstream}   # prints origin/latest
.cli-venv/bin/python -m sandbox.controller_update.refresh   # converge the venv once
```

From then on the checkout fast-forwards along `origin/latest` whenever a
remote reports a newer published revision. Leaving `main` behind is fine;
nothing else in that checkout changes.
