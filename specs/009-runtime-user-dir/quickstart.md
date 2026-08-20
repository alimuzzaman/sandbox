# Quickstart / Validation Guide: Single Swappable Per-User Base

Live-stack validation for spec 009. Each section maps to a user story and is the proof of
done (constitution IV). Run from a registered project dir unless noted. Replace
`<proj>` with a real plugin checkout (e.g. a templately worktree).

## Prerequisites

- Branch `feat/agent-tooling-specs`, this feature implemented.
- At least one existing instance registered (for US1) or a clean base (for US2).
- `jq` / `git` available; Docker running.

## §1 — US1: Existing setup keeps working after upgrade (P1)

```bash
# Baseline: capture current instances + a known site URL BEFORE migrating
./sb instances
# (note one instance + its URL)

# Trigger migration (or run any ordinary command to fire the auto-hook)
./sb migrate            # expect: moves <repo>/runtime + config/secrets under ~/sandbox

# Verify state relocated, repo clean
ls ~/sandbox/runtime    # expect: wp-*, compose, snapshots, registry.json, ...
test -f ~/sandbox/config.json && echo CONFIG_OK
test -f ~/sandbox/sandbox.local.yml && echo LOCAL_OK
git -C . status --porcelain | grep -E 'runtime/|sandbox.local.yml|\.env.local' && echo LEFTOVER || echo REPO_CLEAN

# Verify a previously-registered instance still boots + serves
./sb ensure
./sb wp option get siteurl        # expect: the same URL as baseline
curl -fsS -o /dev/null -w '%{http_code}\n' "$(./sb status --url 2>/dev/null || echo http://localhost:8188)"  # expect: 200
```

Expected: state under `~/sandbox`, `REPO_CLEAN`, instance boots, site serves 200.

```bash
# Idempotency: re-run must be a no-op
./sb migrate            # expect: "already migrated" / no changes, exit 0
```

## §2 — US2: Fresh clone uses the base, no repo pollution (P1)

```bash
# Simulate a fresh environment (use a scratch base to avoid touching real state)
export SANDBOX_HOME="$(mktemp -d)/sandbox"

cd <proj>
./sb ensure             # create/boot an instance from scratch under the scratch base

# All generated state under the base; nothing in the repo
ls "$SANDBOX_HOME/runtime"        # expect: wp-<inst>, compose, registry.json
git -C <sandbox-repo> status --porcelain | grep -E 'runtime/|\.env.local|sandbox.local.yml' && echo POLLUTED || echo CLEAN

# Instance serves
./sb wp option get siteurl
unset SANDBOX_HOME
```

Expected: `CLEAN`; instance state lives only under the scratch base; site serves.

## §3 — US3: Relocate the whole base (P2 — swappability invariant)

```bash
NEW="$(mktemp -d)/sandbox-moved"
SANDBOX_HOME="$NEW" ./sb home "$NEW"     # or: SANDBOX_HOME="$NEW" ./sb migrate

# Instances boot from the new base
SANDBOX_HOME="$NEW" ./sb ensure
SANDBOX_HOME="$NEW" ./sb wp option get siteurl   # expect: 200/correct URL

# Nothing references the old base
grep -rl "$OLD_BASE" "$NEW/runtime/compose" && echo STALE_REF || echo NO_STALE_REF
# venv recreated for new base (shebang points into NEW)
head -1 "$NEW/runtime/.venv-tools/bin/python" | grep -q "$NEW" && echo VENV_OK
```

Expected: instances boot from `$NEW`, `NO_STALE_REF`, `VENV_OK`.

## §4 — CLI ↔ MCP agree on the base (SC-005)

```bash
# CLI base
./sb home                                  # prints resolved base
# MCP base — via an MCP tool call from a Claude session in <proj>:
#   ensure_instance(project_dir=<proj>) then a path-revealing tool (e.g. fs_list)
#   confirm the instance WP dir resolves under the SAME base as `sb home`.
```

Expected: identical base from both surfaces. (Requires a Claude Code restart after the MCP
server code changes — gotcha #4.)

## §5 — Secrets safety (SC-007)

```bash
stat -f '%Lp' ~/sandbox/.env.local        # expect: 600
# migration output must not contain secret values
./sb migrate 2>&1 | grep -iE 'password|token|secret=' && echo LEAK || echo NO_LEAK
```

Expected: `600`, `NO_LEAK`.

## Pass criteria summary

| Check | Maps to |
|-------|---------|
| Migrated state under base, repo clean, instance serves | SC-001, SC-002, US1 |
| Re-run migrate = no-op | SC-006 |
| Fresh clone → all state under base, none in repo | SC-002, SC-003, US2 |
| Base relocation → instances boot from new base, no stale refs | SC-004, US3 |
| CLI & MCP same base | SC-005 |
| `.env.local` 600, no secret leak | SC-007 |

## §6 — Durable workspace metadata/index migration (convergence)

Use an isolated `SANDBOX_HOME` fixture for the metadata migration checks. Keep the
legacy `workspace.json` files under the fixture immutable and do not run reset, destroy,
cleanup, or network-release commands.

```bash
export SANDBOX_HOME="$(mktemp -d)/sandbox"
mkdir -p "$SANDBOX_HOME/runtime/jobs/workspaces/legacy-demo/demo"
# Populate a bounded fixture through the supported workspace/job test helpers; do not
# hand-edit a live user's metadata.
./sb workspace migrate --project-identity PROJECT_ID --json   # plan only
```

Expected plan output includes an opaque plan ID, target project identity, complete
inventory digest, index generation, expiry, and one decision per legacy record. Apply is
allowed only after a rescan confirms the same digest/generation and returns no collision:

```bash
./sb workspace migrate --plan-id PLAN_ID --confirm --json
./sb workspace list --project-identity PROJECT_ID --json
./sb workspace status --workspace-id WORKSPACE_ID --json
```

Verify all of the following:

- `$SANDBOX_HOME/runtime/workspaces/index.sqlite3` is present with stable workspace IDs;
- each source `workspace.json` is byte-for-byte unchanged;
- unresolved, conflicting, malformed, symlink, or oversized sources report
  `workspace_index_incomplete`/an explicit migration decision and are not omitted;
- status still works when the checkout locator is absent;
- relocating the base changes only index/locator paths and preserves database volumes,
  project files, uploads, snapshots, and network/container/job counts;
- resource status consumes typed ownership output and never reads the SQLite file.

The index migration is complete only when repeated plan/apply is idempotent and a changed
inventory or index generation fails closed with `workspace_migration_plan_stale`.
