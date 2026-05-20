# Sandbox — v1 readiness checklist

Track what blocks tagging `v1.0.0`. Items in **Blockers** ship as bug fixes
on `main` before the tag; **Nice-to-have** can slip to v1.1.

Cut `v0.9-beta` today, hand to 2-3 internal testers, work the blockers
from their reports, then cut `v1`.

---

## Blockers (must be done before v1)

- [ ] **Clean-room install verified on a fresh machine.**
      `git clone && ./sb setup` on at least one un-customized
      macOS box and one un-customized Ubuntu box. Capture any failure as
      a follow-up issue.
- [ ] **Smoke test exists.** Either a `./sb smoke` subcommand
      (boots stack → installs WP → activates a known plugin → REST ping →
      tears down) or a GitHub Action that runs setup on every PR.
- [ ] **`gh` org detection is correct.** Today `connect gh` saves
      `gh api user .login` as `defaults.github_org`, which is the personal
      handle. Most devs need `wpdeveloper` instead. Fix: list orgs from
      `gh api user/orgs`, prompt to pick (default = first), fall back to
      the personal handle.
- [ ] **Phase 2/3 toggles labelled honestly.** `mcp.browser.enabled` and
      `mcp.figma.enabled` in `sandbox.yml` look usable but aren't wired.
      Either gate them behind an explicit `# not implemented yet` comment
      or rip them out until Phase 2 lands.
- [ ] **`apply` vs `setup` disambiguated.** README mentions both; they're
      the same handler. Keep `setup` as primary, label `apply` as `(alias
      of setup)` in `--help` and remove the duplicate README mention.
- [ ] **`doctor` covers `connect` state.** Add checks for: FluentBoards
      URL reachable (HEAD request, optional), GitHub org set if any
      `add` is used, `.env.local` exists + chmod 600.
- [ ] **Version + CHANGELOG.** Add a top-level `VERSION` file (or read
      from `sandbox.yml`'s `version:` key), a `CHANGELOG.md` with v1
      notes, and a `git tag v1.0.0` once everything below is green.

## Nice-to-have (won't block v1)

- [ ] **Workflow library has at least 2-3 *used* (not just authored)
      end-to-end flows.** Vision promises "ship plugins faster" — that
      needs proof. Document a real shipped fix using `workflows/`.
- [ ] **`connect` accepts env-var override** (e.g.
      `FLUENTBOARDS_APP_PASSWORD=… ./sb connect fb
      --non-interactive`) so CI / scripted setups don't need a TTY.
- [ ] **Per-OS install scripts** (`scripts/install-macos.sh`,
      `scripts/install-ubuntu.sh`) that handle Docker + Python in one
      shot for newcomers who don't have either.
- [ ] **MCP server hot-reload.** Right now editing `mcp/wp-server/server.py`
      requires reopening Claude Code. A file-watcher restart would help
      anyone extending the toolset.
- [ ] **Telemetry / opt-in usage ping** so we know which subcommands and
      MCP tools are actually used in practice.

## Done (already shipped)

- [x] Polyglot bootstrap catches missing python3 with install hint.
- [x] Preflight checks Docker daemon, `docker compose` v2, Python 3.9+,
      `python3 -m venv` module.
- [x] `setup` is non-interactive; secrets are opt-in via `connect`.
- [x] Secrets land in gitignored `sandbox.local.yml` + chmod-600 `.env.local`.
- [x] CLAUDE.md has operating rules + efficiency/accuracy/security pillars
      covering both sandbox tooling and plugin-code work.
- [x] Snapshot / restore / xdebug / doctor / update / open subcommands.
- [x] Symlink depth + bind-mount path bugs fixed (plugins land at
      `wp-content/plugins/<slug>` and resolve correctly).

---

## Cut-the-tag flow (once Blockers are green)

```bash
./sb doctor                       # all green
./sb smoke                        # all green (once added)
git tag -a v1.0.0 -m "v1.0.0"
git push origin main --tags               # ask before pushing
```
