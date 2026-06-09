# Sandbox — v1 readiness checklist

Track what blocks tagging `v1.0.0`. Items in **Blockers** ship as bug fixes
on `main` before the tag; **Nice-to-have** can slip to v1.1.

Cut `v0.9-beta` today, hand to 2-3 internal testers, work the blockers
from their reports, then cut `v1`.

---

## Blockers (must be done before v1)

- [x] **Clean-room install verified on a fresh machine.**
      `.github/workflows/smoke.yml` runs `./sb smoke` on every push/PR
      to `main` using a GitHub Actions `ubuntu-latest` runner (fresh VM,
      Docker + Python pre-installed). First green run on `main` is the
      acceptance gate.
- [x] **Smoke test exists.** `./sb smoke` boots a fresh instance, checks
      WP installed + REST probe, tears down.
- [x] **`gh` org detection is correct.** `connect gh` now lists all orgs,
      prioritises WPDevelopers, and prompts to pick — never silently saves
      the personal handle.
- [x] **Phase 2/3 toggles labelled honestly.** `mcp.browser.enabled` and
      `mcp.figma.enabled` now carry `# not implemented yet` comments.
- [x] **`apply` vs `setup` disambiguated.** `apply` is labelled
      `Alias for setup` in `--help`; README mentions only `setup`.
- [x] **`doctor` covers `connect` state.** Checks FluentBoards reachability
      (if configured), `github_org` set, `.env.local` chmod 600.
- [x] **Version + CHANGELOG.** `VERSION` file + `CHANGELOG.md` added;
      `git tag v1.0.0` happens once the remaining blocker is green.

## Nice-to-have (won't block v1)

- [x] **Workflow library has at least 2-3 used end-to-end flows.**
      Three workflows shipped: `fast-plugin-ship` (quick loop),
      `build-feature` (three-phase feature playbook), `ship-fix`
      (ticket → fix loop → branch → PR → card close). `ship-fix`
      references three real fixes from this repo's git history as
      calibration examples.
- [x] **`connect` accepts env-var override** — `./sb connect fb -n` reads
      `FLUENTBOARDS_URL/EMAIL/APP_PASSWORD` from env; `./sb connect gh -n`
      reads `GITHUB_ORG`. Fails fast if required vars are missing.
- [x] **Per-OS install scripts** — `scripts/install-macos.sh` (Homebrew →
      python3 → Docker Desktop) and `scripts/install-ubuntu.sh` (apt →
      Docker CE) both hand off to `./install.sh` after prereqs are set up.
- [ ] **MCP server hot-reload.** Requires Claude Code to re-initialize the
      MCP connection after the server restarts — client-side behaviour we
      can't control from here. Needs upstream support (or a stdio-level
      wrapper that Claude Code explicitly handles). Deferred.
- [ ] **Telemetry / opt-in usage ping.** Needs product design (what to
      record, where to send, opt-in UX). Not a quick code item. Deferred.

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
