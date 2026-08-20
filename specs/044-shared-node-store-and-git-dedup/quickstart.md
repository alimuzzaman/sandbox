# Quickstart: proving shared node storage and history-safe workspaces

This guide is a validation plan for the implementation described by Spec 044. The new
materializer, node-store opt-in, tests, and documentation are not implemented yet, so the
commands below are release-gate scenarios rather than evidence of a passing feature.

## Preconditions and guardrails

- Work from the feature's active non-`main` branch with a clean, reviewable source snapshot.
- Use a disposable source/workspace label and a configured remote only after the implementation
  has passed its local tests. Do not touch a hosted site, DNS/ACME, secrets, or unrelated
  volumes.
- The remote filesystem must be able to report used-space deltas and run `git fsck --full`.
- The project descriptor must opt in explicitly with `compose.nodeStore: true` for node-store
  scenarios. Keep one control project (or a copy of the descriptor) without the key.
- Record commands, exit statuses, bounded JSON/Compose config, and timestamps. Do not infer a
  byte saving, source integrity, or permission success from a mocked runner.

## 1. Local static and focused checks (no host mutation)

After the implementation adds the planned seams, run the focused tests:

```bash
python -m unittest \
  tests.test_workspace_git_dedup \
  tests.test_compose_node_store
```

Expected assertions include private Git metadata inodes, hardlinked eligible object/pack
files, cross-device/plain-copy fallback, marker-file rewriting, source integrity after each
workspace operation, strict `compose.nodeStore` normalization, family equality/difference,
one explicit named volume, exact environment values, legacy overlay byte identity, and
read-only/confirmation-gated reclaim. A test runner that cannot import a planned module is a
bootstrap failure, not feature evidence.

Run the repository's static checks on the changed implementation and docs:

```bash
git diff --check
```

The static check catches malformed whitespace only; it does not prove remote behaviour.

## 2. Git materialization fixture

Use a temporary Git fixture with at least one loose object, one packed object, a branch/ref,
reflog, index, config, and an untracked file. Invoke the new local materializer through its
public test seam, not by copying the directory by hand.

Verify all of the following before proceeding:

```bash
git -C "$SOURCE" status --porcelain=v1
git -C "$SOURCE" diff --exit-code
git -C "$SOURCE" fsck --full
```

- Worktree bytes match the source.
- Eligible object/pack regular files have the same inode (`stat`) while `HEAD`, index, refs,
  logs, config, hooks, marker, and `objects/info` do not.
- Workspace branch/ref/reset/discard operations update only workspace-private files.
- Injecting `EXDEV`/unsupported link errors selects `history_mode=copied`, leaves no staged
  links, and still permits `git rev-parse`/`git fsck`.
- A valid `.git` marker file is rewritten to a private administrative directory. Delete the
  source fixture and verify the workspace still discovers its root, derives its instance
  identity, and reads its history. Restore the fixture from the disposable copy afterwards.
- Hold the source lock while starting a second refresh; expect
  `workspace_materialization_busy` and an untouched first workspace.

Repeat the source checks after each deploy reset, untracked discard, dirty-layer unpack, test,
and build seam. The source must continue to report no tracked modifications and a successful
full fsck. If any operation changes source bytes, metadata, refs, or integrity, stop: this is a
release blocker.

## 3. Remote history-space evidence (required, not yet run)

Use a real configured remote only after steps 1–2 pass. The exact commands depend on the
deployed project and must use a finite deadline:

```bash
./sb deploy --remote NAME --json
# Capture a read-only capacity/used-space observation using the supported Sandbox remote path.
./sb exec --remote NAME --workspace spec044-history-check \
  --timeout 1200 --detach -- python3 -m unittest tests.test_workspace_git_dedup
./sb job-status JOB_ID --remote NAME --json
./sb job-output JOB_ID --remote NAME --stream combined --max-bytes 65536
```

The implementation's evidence script must create exactly one temporary workspace from the
deployed source, capture used bytes before/after, record `history_mode` and link counts, run
the reset/discard/unpack/test/build sequence, and remove only that temporary workspace through
its supported lifecycle. Capture source `git status --porcelain=v1`, `git diff --exit-code`,
and `git fsck --full` before and after. The required success criterion is the measured SC-001
delta on the real host, not an estimate or local fixture size. No result is claimed here.

## 4. Opted-in Compose overlay

In the disposable project's own descriptor, add only the explicit opt-in (the project must
also point its dependency tree at the supplied path and remove its old per-workspace
dependency volume):

```json
{
  "compose": {
    "nodeStore": true
  }
}
```

Start two labels from the same deployed source, then inspect the generated Compose config
through the supported runtime/remote command. The evidence must show:

```text
sandbox-nodestore-<one-family-id>:/sandbox-node
SANDBOX_NODE_STORE=/sandbox-node/store
SANDBOX_NODE_MODULES=/sandbox-node/node_modules
npm_config_store_dir=/sandbox-node/store
```

Both labels must report the same exact named volume; a second named volume or a host bind for
store/modules is a failure. Run concurrent installs of different dependency versions and
confirm both jobs finish without corrupting either dependency tree. Then prepare/remove the
host-visible workspace as the ordinary operator account while the container writes the named
volume; a permission failure is a failure.

Repeat the same inspection with the control descriptor lacking `nodeStore`. Its generated
overlay bytes must match the legacy fixture exactly and contain no node-store volume,
environment variable, or package-cache change. An image that ignores the variables must still
start and install at its old cost; Sandbox must not fail preflight or run an inferred package
script.

## 5. Legacy workspace and rollback proof

Point the new runtime at one pre-feature workspace and run its existing start/status/reset
path without migration. Expected: the operation succeeds and the old copy/volume remains
usable. Do not delete it as part of this proof.

For a disposable opted-in workspace, rehearse rollback:

1. Stop the service and capture a named, read-only migration/reclaim plan.
2. Set `compose.nodeStore=false` and restore the project's old dependency mount/command.
3. Start/reset/status again and verify the legacy path works.
4. Leave both old workspace data and the shared volume until the operator explicitly accepts a
   separate reclaim plan.

No migration step is required for ordinary operation, and no rollback step may invoke broad
cleanup.

## 6. Named shared-store reclaim (explicit confirmation only)

There is no named-store reclaim interface in the current Sandbox code. Raw Docker inspection,
process listing, or volume removal is unsupported by the Sandbox workflow and must not be used
as a workaround. The implementation must first add a supported Sandbox CLI/MCP surface (see
T015 in `tasks.md`) with two explicit phases:

1. **Read-only plan**: accept a validated family id, resolve the exact
   `sandbox-nodestore-<family>` name, report existence/size and running-container mounts,
   and persist a named plan id. This phase performs no removal or workspace mutation.
2. **Confirmed apply**: require that plan id plus explicit `--confirm` (or the equivalent MCP
   confirmation), re-check the exact volume and running mounts, and remove only the planned
   disposable store. A missing volume is reported as `already_absent`; a race or mount refuses
   the apply.

Do not attempt this section until that supported interface exists and its tests/evidence pass.
Never use `docker volume prune`, a wildcard, an inferred family, or a reclaim call from
`ensure`, `status`, job cleanup, capacity pressure, or workspace destroy. A follow-up install
must repopulate the missing store; the evidence must report `already_absent`/repopulated
semantics honestly rather than claiming data recovery.

## Evidence boundary

This quickstart intentionally makes no claim about implementation status, remote host
measurements, permission outcomes, performance, or release readiness. Those remain required
gates for the implementation handoff.
