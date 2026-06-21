# Quickstart: Validate Per-Project-First & Modular `sb`

Live verification (constitution Principle IV). Run from the worktree
`/Users/alim/Sites/git/sandbox-per-project-modular`. Use a real registered instance — e.g.
`templately-fsi-rewrite` (running) — and the unregistered sandbox dir for the error path.

## Prerequisites

- At least one registered, running instance (`./sb instances` shows it).
- The worktree's `./sb` on PATH or invoked by absolute path.

## Stage A — app-password parity (run after Stage A)

1. For each registered instance: `./sb doctor` → **Expected**: "application_password set" OK
   (now read from `instances.<name>.app_password`, no `main` legacy key).
2. MCP: a password-needing tool (e.g. `wp_rest`) against a registered project authenticates.
   **Expected**: success — parity preserved with `main` still present.

## Stage B — no `main`, error-on-no-project (run after Stage B)

1. From a registered project dir: `./sb status`, `./sb wp plugin list`, `./sb doctor` →
   **Expected**: target that project's instance.
2. From the sandbox dir (unregistered): `./sb status` → **Expected**: exits non-zero with
   guidance ("cd into a registered project or run `sb init`/`sb ensure`") — NOT a `main` boot.
3. `--instance <name>` and `SANDBOX_INSTANCE=<name> ./sb status` still override the cwd.
4. `./sb instances` → **Expected**: no `main` row.
5. Delete an instance → **Expected**: no "refusing to delete main" guard.
6. Grep clean (SC-002):
   `grep -n 'DEFAULT_INSTANCE\|migrate_legacy' sb mcp/wp-server/server.py` → no load-bearing
   hits; audit `"main"` similarly.
7. `python3 sandbox_core.py --selftest-registry` → passes.

## Stage C — modular package, identical behavior (run after Stage C)

1. `python3 -c "import ast; ast.parse(open('sb').read())"` → no error.
2. `python3 -c "import sandbox.cli"` (with ROOT on path) → imports clean.
3. Re-run the Stage B matrix from a project dir AND via the global `sb` symlink → identical.
4. `scripts/build-web-js.sh` then `./sb web` → lists instances with delete enabled for ALL
   (no `main` guard).
5. Release tarball dry-run → contains `sandbox/`; `.specify/` + `skills/speckit-*` are pruned.

## Acceptance (SC-001..SC-005)

- SC-001: bare command in a project dir targets that instance (no `main`).
- SC-002: grep finds zero load-bearing legacy refs.
- SC-003: command matrix equivalent before/after for a registered instance.
- SC-004: installed CLI (symlink + tarball) runs identically from any dir.
- SC-005: each feature locatable in its own module; `sb` is the thin entry.

## References

- Resolution + registry interface: [contracts/cli-contract.md](./contracts/cli-contract.md)
- Entities: [data-model.md](./data-model.md) · Decisions: [research.md](./research.md)
