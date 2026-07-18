# Sandbox

A real WordPress environment for designers, developers, and QA at WPDeveloper —
drivable by Claude Code (or any MCP client: Cursor, Cline, Continue, Zed).

## Scoped recovery

Recovery is profile-driven through `sb recovery`. Capture, restore apply, retention deletion,
and schedule activation are protected; see [docs/recovery.md](docs/recovery.md).

## Extension boundaries

Sandbox keeps public CLI and MCP behavior stable while feature ownership is modularized:

- project descriptors select `kind` before runtime-specific defaults; omitted `kind` remains `wordpress`;
- registry identity and atomic persistence live behind the project-registry repository;
- runtime capabilities reject unsupported work before process, network, proxy, or registry side effects;
- CLI commands and MCP tool groups are owned by explicit deterministic manifests;
- shared process, HTTP, port, path, and proxy services own mechanisms, while adapters own runtime policy;
- Hermes state, routing, jobs, gateway, and backup planning are bounded modules.

`sandbox_core.py`, `sandbox.registry.COMMANDS`, `sandbox.hermes.facade`, and the MCP
`app.py` helper namespace are compatibility/rollback paths, not extension points.
New code must use the bounded service or registration contract. Their consumer sets
are frozen by architecture tests; removal requires parity evidence and separate
human approval.

**CLI-first, per-project, and MCP-optional.** Each plugin repo carries its own
`sandbox.config.json`. You `cd` into a plugin, and a single MCP server boots a
WordPress instance for that directory on demand and runs the plugin's **real
phpunit tests** — no central catalog, nothing to pre-register.

---

## Get started

> **Note:** This is a major rewrite to the per-project model hosted at
> [`templately/sandbox`](https://github.com/templately/sandbox). Install:

**Prerequisites:** A running Docker-compatible engine (Docker Desktop or
OrbStack on macOS) · Python 3.9+ · Claude Code (or any MCP client). On a fresh
machine, run the OS bootstrap script first:

```bash
# macOS
bash scripts/install-macos.sh   # Homebrew → python3 → Docker Desktop/OrbStack → Reader.md

# Ubuntu / Debian
bash scripts/install-ubuntu.sh  # apt (python3+venv) → Docker CE

# Arch Linux (and derivatives: Manjaro, EndeavourOS)
bash scripts/install-arch.sh    # pacman → python → docker + docker-compose
```

Other Linux distros (Fedora/RHEL, openSUSE, etc.) work too — `./sb setup`
detects `dnf`/`zypper` and offers the right install commands automatically;
there's just no dedicated one-shot bootstrap script for them yet. **Windows**
isn't supported natively (the CLI is a POSIX shell + Python tool, and relies
on Docker Unix sockets and process groups/signals) — run it inside **WSL2**
instead, where it behaves exactly like the Ubuntu path above.

**Clone and set up:**

```bash
git clone -b main https://github.com/templately/sandbox.git
cd sandbox
./sb global           # puts `sb` on your PATH (do this first)
./sb setup            # prepares the CLI and local runtime
./sb guide            # show the runtime-aware CLI catalog
./sb domains setup    # optional: clean no-port URLs → https://<name>.<tld>
```

`setup` offers to install missing prerequisites (default always **No**)
and never needs `sudo` for the base install.

On macOS, the bootstrap also installs [Reader.md](https://github.com/jnahian/reader.md)
by default when Homebrew is available. It provides the `reader` command for
opening local Sandbox documentation and read-only remote documentation folders.
Set `SANDBOX_SKIP_READER_MD=1` before running the bootstrap to opt out; a
Reader.md failure only warns and never prevents Sandbox setup.

Reader.md is maintained in its own Homebrew tap. The bootstrap scopes
Homebrew's required trust grant to its `reader-md` cask before installation;
review that upstream tap if your environment disallows third-party casks.

### Reader.md for agents and operators

Reader.md is an optional **local, visual reading surface**. An agent on the
macOS workstation may open a known local Markdown file or folder when that
helps the operator review documentation:

```bash
reader /absolute/path/to/spec.md
reader /absolute/path/to/folder
```

It is not an MCP server and its window is not evidence an agent can inspect.
Use `fs_read`, repository reads, or `ssh` for machine-readable evidence and
tests. Do not use `reader remote` or `reader rm` from an agent: the former
adds an SSH-backed application connection and the latter removes saved Reader
configuration. Those remain explicit operator commands. `reader ls` is safe
for an operator to inspect configured Reader roots.

**`domains setup` asks which local TLD to use** (or pass it directly:
`./sb domains setup tst`), defaulting to **`tst`**. Instances then serve at
`<name>.<tld>` (e.g. `https://myplugin.tst`). Avoid `.sb` (a real ccTLD) and
`.test` (owned by Herd/Valet).

A project can pin its own TLD with `"tld": "<your-tld>"` in its
`sandbox.config.json` (overrides the prompt for that project):

```jsonc
// sandbox.config.json
{ "tld": "tst" }   // ← omit for the tst default
```

`domains setup` is optional — without it, instances still work at
`http://localhost:<port>`.

Running `./sb global` first means the MCP registration uses `sb` (PATH-based,
like `@wordpress/env`) rather than a hardcoded absolute path — so the
registration survives the repo being moved or re-cloned.

`setup` registers **one** MCP server named `sandbox` at user scope so
**every** `claude` session on the machine has it — from any directory:

```bash
claude          # in any project, in any dir
```

That single server routes by the `project_dir` every tool receives — there are
no per-instance servers to manage.

---

## The per-project model

A plugin repo carries a **`sandbox.config.json`** describing its stack:

```jsonc
{
  "plugins":   ["."],                 // this repo; sibling slugs/paths/zip-URLs for addons
  "mappings":  { "wp-content/plugins/elementor-pro": "/abs/path" },
  "phpVersion": null,                 // null → wordpress:latest; e.g. "8.1"
  "wpVersion":  null,                 // e.g. "6.4"
  "server":     "apache",             // apache | nginx | litespeed
  "config":     { "WP_DEBUG": true }, // → wp-config constants
  "tests":      { "suite": "auto" }   // auto-detect WP_UnitTestCase vs Brain/Monkey
}
```

(An existing **`.wp-env.json`** is read as a fallback and converted on
`sandbox init`. Full schema: [`docs/sandbox-config-reference.md`](docs/sandbox-config-reference.md).)

Generic PHP, JavaScript/Node, Docker, Laravel/Sail, Astro, and similar projects
can use the same framework-neutral Compose runtime by declaring `kind: compose`
and their public service in `sandbox.config.json`. See the
[generic Compose configuration reference](docs/sandbox-config-reference.md#generic-compose-projects).

Then, from the plugin directory:

```bash
cd ~/dev/embedpress
sandbox init      # scaffold sandbox.config.json (or convert .wp-env.json),
                  #   boot a per-directory instance, provision the test harness
sandbox test      # auto-select unit or integration mode and run PHPUnit
sandbox test unit       # pure PHPUnit; skips WP suite, polyfills, and test DB
sandbox test integration # externally-provisioned WP suite + isolated test DB
sandbox ensure    # just boot/refresh this project's instance (create-if-missing)
```

`init` is the one command from a bare checkout to a running, testable stack.
Each project gets **one instance by default**, keyed by its directory and
tracked in an on-disk registry. Sibling plugins listed in one config share
that instance. A project can also own additional labelled instances side by
side (e.g. to test a second PHP/WP version, or a zip install alongside dev) —
pass `--label <name>` / `label=` (default `default`); see
`docs/multi-instance-spec.md`.

**With Claude, you don't even run those** — the MCP tools take `project_dir`
(the agent passes your plugin dir), and `ensure_instance` boots on demand. Just
work in the plugin and ask Claude to test/fix/build.

### The test harness (the core value)

Sandbox provides the WP test suite, phpunit, the Yoast polyfills, composer, and
an isolated `wp_tests` database **externally** for integration tests — mounted
only at test time — so a plugin's `composer.json` stays clean. `sandbox test`
resolves `tests.suite` (`auto`, `unit`, or `integration`); auto selects unit only
for unambiguous Brain/Monkey-only evidence and conservatively selects integration
otherwise. Unit mode uses project Composer dependencies and PHPUnit without the WP
suite, polyfills, test DB, or `WP_TESTS_*` environment. The `run_tests` MCP tool
accepts the same optional `mode` and returns the resolved mode with its summary.

Version pins resolve server-aware: `phpVersion: "8.1"` boots `wordpress:php8.1`
on apache, the `-fpm` flavor on nginx, and an OpenLiteSpeed `lsphp81` image on
litespeed; the wp-cli container (where tests run) follows the PHP pin too.

---

## Plain Claude vs. Claude + sandbox

Claude in your IDE is already smart. It can read your code, propose diffs, talk
through architecture. What it **cannot** do alone is run your WordPress, see
what your block actually renders, query your DB, check `debug.log`, or know your
plugin's specific conventions. It's a brilliant pair-programmer working
blindfolded against an unfamiliar codebase. The sandbox removes the blindfold
and hands it the keys.

### What plain Claude has

- Your source code on disk (Read / Write / Edit).
- The internet (web search, fetch).
- Its training knowledge of WordPress / PHP / JS.
- Nothing about *your* WordPress, *your* plugin's conventions, or whether the
  edit it just made actually works.

### What Claude + sandbox has, on top of that

- **A live WordPress** with your plugin symlinked in. Edits land in seconds, no
  rebuild. The agent acts on the stack instead of guessing at it.
- **Real tests on demand** — `run_tests` runs the plugin's phpunit suite against
  an externally-provisioned WP test harness, so "it works" is backed by a green
  run, not a `php -l`.
- **Your plugin's institutional knowledge** auto-loaded. The project's
  `CLAUDE.md` (textdomain rules, `save()` BC traps, build conventions,
  task-tracker board, sister-repo location) reaches the model via `focus_get`.
- **A compact operating prompt** in every Claude session via the MCP
  `instructions` field — reflexes ("first tool call reproduces, not Read"),
  anti-patterns ("declaring fixed from code reading"), the project handshake
  (always pass `project_dir`; call `ensure_instance` first). Deeper guidance
  loads on demand via `load_context` / `load_skill(name)`.
- **Skills + workflows** for the patterns that repeat: `fix` for bugs (one-pass
  loop with paired before/after evidence), `build-feature` for new features
  (three-phase, size-scaled gates), `wp-pilot` for browser-driven admin testing,
  `fluentboards` for task management.

### What that means on three tasks you actually do

**Fix a bug in your plugin.**

| Step | Plain Claude | Claude + sandbox |
|------|--------------|------------------|
| **Understand** | Asks you the version, the active plugins, the theme. | The project's `CLAUDE.md` is already in context; can fetch the task-tracker card via REST in one call. |
| **Reproduce** | "Let me look at the file" → guesses the cause; can't verify. | First tool call provisions whatever the bug needs and triggers it on the live WP; captures the real error as `EVIDENCE.before`. |
| **Find every site** | Reads the file the report names; misses the Pro-side mirror. | Greps every call site across the plugin AND its `-pro` sibling in one pass. |
| **Fix** | Edit, ask you to test, edit again. 3–5 rounds. | Batch-edits every affected file in one pass. |
| **Verify** | "Looks right," or `php -l`. | Re-triggers the failing call → confirms the output flipped → `EVIDENCE.after`. Or `sandbox test` → green. |
| **Ship** | Stops at the working tree. | Commits and pushes verified completed work on the active branch automatically. |

**Build a new feature.** `load_workflow('build-feature')` → Phase 1 ESTABLISH
(verb-led title, size class, live-verifiable success criteria, out-of-scope,
edge cases) → Phase 2 PLAN (reuse audit naming every existing helper/table/route
it'll ride on; cross-surface grep) → Phase 3 BUILD (vertical slices, each
verified by an `sb` CLI/MCP call; non-negotiables — auth, sanitize-in/escape-out, slug
prefixing — enforced per Edit). Final `STATUS: SHIPPED` block pairs every
success criterion with live evidence + rollout notes.

**Verify a UI flow.** `visit` opens a real admin or frontend URL and returns a
screenshot, DOM, and console errors without you switching tabs.

### The two underlying patterns

1. **Live evidence is the only evidence.** Every "fixed" / "shipped" /
   "verified" is backed by an `sb` CLI/MCP call (or a test run) against the running
   WordPress — not a claim from reading code.
2. **Verified changes ship as a normal Git update.** Sandbox commits and pushes
   the active branch after required checks. Force-pushes, tags, releases,
   deployments, and PR actions remain explicit.

---

## CLI-first operation (MCP optional)

Use the same runtime operations without an MCP client:

```bash
./sb guide --project-dir .        # runtime-aware command catalog
./sb skill show sandbox-cli       # CLI-first operating skill
./sb ensure                       # start/reconcile local instance
./sb exec -- sh -lc 'npm test'    # generic Compose projects only
./sb deploy --remote <name> --ensure --expose
```

`./sb mcp --project-dir .` remains available for an MCP-capable client. It is
runtime-scoped: generic Compose projects do not load WordPress tools, and
WordPress projects do not load generic container-exec tools.

## What Claude can do — the MCP tools

After `setup`, the single `sandbox` server exposes these against the live stack.
**Every tool takes `project_dir`** (the agent passes your plugin's root, or cwd)
and resolves the target instance from the registry — booting one if needed.

| Tool | Purpose |
|------|---------|
| `ensure_instance` | Boot (create-if-missing) the instance for a project dir; returns its URL |
| `destroy_instance` | Permanently delete an instance (containers, DB volume, wp dir, registry) |
| `recreate_instance` | Destroy then immediately recreate — clean WP install from current config |
| `run_tests` | Run the plugin's phpunit tests on the external WP harness → pass/fail + failures |
| `run_plugin_check` | Run WordPress.org's Plugin Check, gated by a committed baseline → pass/fail + new findings (see `docs/plugin-check.md`) |
| `remote_deploy` | One-way, on-demand push of local project state to a registered remote VPS (see `docs/remote-hosting.md`) |
| `wp_cli` | Run any `wp` command |
| `wp_exec` | Arbitrary shell in any container (composer, npm, php, …) |
| `wp_rest` | Call the WordPress REST API (pre-wired app password) |
| `http_fetch` | Lightweight anonymous HTTP probe — status, headers, body, redirects |
| `visit` | Headless Chromium; auto-logs in on `/wp-admin/`. Returns status + DOM + iframes + console + network + optional screenshot |
| `db_query` | Run SQL — writes require `mutate: true` |
| `tail_log` | Tail `wp-content/debug.log` |
| `fs_read` / `fs_write` / `fs_list` | Read/write files under the instance's WP dir |
| `mail_list` / `mail_get` | Read Mailpit (test SMTP inbox) |
| `focus_get` | The project's focused plugin and available skills; pass `include_claude_md=true` when the project guide is needed |
| `activate_plugin` / `deactivate_plugin` | Toggle plugins |
| `import_content` | Import a WXR XML from `runtime/seeds/` |
| `load_context` | Pull the full sandbox `CLAUDE.md` on demand |
| `load_skill` | Pull a skill (`fix`, `bug-repro`, `snapshot`, `wp-debug`, `wp-pilot`, `fluentboards`) |
| `load_workflow` | Pull a workflow (`build-feature`) |

Plus Claude's normal `Read`/`Write`/`Edit` reach the plugin source on disk —
bind-mounted into the container, so edits are live with no rebuild.

You can also invoke skills as slash commands, e.g.
`/mcp__sandbox__activate` (load the full operating guide) or
`/mcp__sandbox__fix <task>` (one-pass bug-fix loop).

---

## Managing instances

Instances are created per-project by `init`/`ensure` — there's no
`instance create`. But you can view and drive them:

```bash
./sb instances            # list every per-project instance + status + URL
./sb dashboard            # full-screen TUI: start/stop/restart/open/focus/delete
./sb web                  # the same dashboard in the browser (127.0.0.1:8765)
./sb instance delete <name>   # tear one down (containers, volume, files, registry)
```

Each instance can run a different **web server**, and you can switch in place
without re-importing content:

```bash
./sb server <name> nginx        # apache → nginx (adds the nginx sidecar)
./sb server <name> litespeed    # → OpenLiteSpeed
./sb server <name> apache       # → back to apache
```

### Clean URLs — `https://<name>.tst`

By default instances serve at `http://localhost:<port>`. Upgrade to a trusted,
no-port HTTPS URL with one optional setup:

```bash
./sb domains setup     # one-time, asks your password ONCE (installs a local CA)
./sb secure <name>     # mint a trusted cert for one instance → https://<name>.tst
```

It coexists with Laravel Valet (separate loopback IP). Undo with
`./sb domains teardown`.

---

## Daily commands

```bash
sandbox init              # in a plugin dir: config + instance + test harness
sandbox ensure            # boot/refresh this project's instance
sandbox test [-- <args>]  # run the plugin's phpunit tests (pass extra phpunit args after --)
./sb focus <plugin>       # mark which plugin is focused (for Claude)
./sb open [admin|site|mail]  # open in browser (default: admin)
./sb visit <url> [...]    # load URL in headless Chromium, report DOM/console/iframes
./sb snapshot <name>      # save DB + uploads (fast bug repro / QA)
./sb restore <name>       # restore a saved snapshot
./sb update               # git pull the project repo this instance tracks
./sb xdebug on|off        # toggle step-debug (port 9003, host trigger)
./sb doctor               # audit the stack
./sb status               # which containers + project + focus are active
./sb down                 # stop containers (state preserved)
./sb clean                # stop + wipe DB volume (start fresh)
```

Run `./sb` with no args for the full list. Most of these accept
`--instance <name>` (or `--project-dir <dir>` for `ensure`/`test`/`init`) to
target a specific project.

---

## Configuration

Two layers:

- **Per-project** `sandbox.config.json` (in the plugin repo, canonical) +
  gitignored `sandbox.config.override.json`. This is what makes a plugin a
  sandbox project. See [`docs/sandbox-config-reference.md`](docs/sandbox-config-reference.md).
- **Machine/global** [`sandbox.yml`](sandbox.yml) — ports base, admin creds,
  image defaults. Per-machine overrides go in the gitignored `sandbox.local.yml`:

```yaml
defaults:
  plugins_home: "$HOME/dev"     # where cloned plugins live
  github_org: "wpdeveloper"
```

There is **no central project catalog** — each plugin self-describes.

---

## Bringing your own CLAUDE.md and skills

Three attach points, all automatic:

1. **Sandbox `CLAUDE.md`** — the operating guide, loaded on demand via
   `load_context` (the compact summary ships every session via the MCP
   `instructions` field).
2. **Project `CLAUDE.md`** — a plugin repo's own `CLAUDE.md` (+ any
   `.claude/skills/<area>/SKILL.md`) is surfaced by `focus_get` for that project.
3. **Personal skills** — `~/.claude/skills/*/SKILL.md` are loaded by Claude Code
   itself, alongside the sandbox.

**Skills** (loaded via `load_skill('<name>')`): `fix`, `bug-repro`, `snapshot`,
`wp-debug`, `wp-pilot`, `fluentboards`. **Workflows** (`load_workflow('<name>')`):
`build-feature`. Each lives in its own folder with an uppercase entry file
(`skills/<name>/SKILL.md`, `workflows/<name>/WORKFLOW.md`).

---

## What lives where

```
sandbox/
├── sb                      # the CLI (Python — invoke as ./sb or `sandbox`)
├── sandbox_core.py         # shared core: per-project config + registry
├── sandbox.yml             # machine/global defaults
├── sandbox.local.yml       # per-machine overrides (gitignored)
├── bin/sandbox.js          # npm entry shim (execs the bundled sb)
├── package.json            # npm package (@templately/sandbox)
├── packaging/              # Homebrew formula + packaging notes
├── docker-compose.yml      # managed by the CLI
├── runtime/
│   ├── wp-<instance>/      # each instance's WordPress install (bind-mounted)
│   ├── registry.json       # project-root → instance mapping
│   ├── test-suite/         # cached wordpress-develop phpunit suite
│   ├── test-tools/         # phpunit + composer phars + polyfills + wp-tests-config
│   └── seeds/              # demo content / WXR imports
├── plugins/                # default home for cloned plugin repos (gitignored)
├── mcp/wp-server/          # the Python MCP server + its venv
├── skills/<name>/SKILL.md  # role packs
└── workflows/<name>/WORKFLOW.md
```

The only state outside this folder: Docker's named volumes (cleared by
`./sb clean` / `./sb instance delete`).

---

## Troubleshooting

```bash
./sb doctor       # checks containers, WP, REST auth, MCP venv, symlinks, project, focus
```

- **REST auth fails** — re-run `./sb ensure` (regenerates the app password).
- **MCP server not connected** — `claude mcp list` should show `sandbox` as
  `✓ Connected`. If missing, re-run `./sb setup`. For the project-local
  fallback, `cat .mcp.json` (it points at `./sb mcp`).
- **A plugin "isn't found"** — make sure you've run `sandbox init` (or `ensure`)
  in its directory so it has a `sandbox.config.json` + a registered instance.
- **Container won't start** — `./sb ensure` resumes a stopped/half-booted
  instance in place; if Docker itself restarted (e.g. an auto-update), relaunch
  Docker and re-run `ensure`.
- **Fresh start** — `./sb instance delete <name>` then `sandbox init` again.

For everything else, ask Claude — it has `tail_log`, `wp_exec`, and `db_query`
and can usually diagnose itself.

---

## Roadmap

- **Shipped** — Docker WP stack; the single `sandbox` MCP server routing by
  `project_dir`; per-project `sandbox.config.*` + on-disk registry;
  externally-provisioned phpunit harness (`sandbox test` / `run_tests`);
  `sandbox init`; server-aware version pins; headless Chromium with auto-login
  (`visit`); size-scaled `build-feature` workflow; one-pass `fix` skill;
  FluentBoards integration; Plugin Check; first-pass remote VPS hosting; managed
  Compose-host validation and confirmation-gated permanent Cloudflare DNS/TLS deployment;
  personal `~/.zshrc.secrets` support; npm +
  Homebrew + curl distribution.
- **Next** — protected recovery and Hermes/Lenzora acceptance remain operator-gated.
  Use the consolidated [release-readiness checklist](docs/release-readiness.md)
  before a release, then see [`docs/future-roadmap.md`](docs/future-roadmap.md)
  for deferred product work.

Re-run `./sb setup` after a global config change — it's idempotent.
## Hermes Agent

Remote Hermes control is documented in [docs/hermes-agent.md](docs/hermes-agent.md).
Its optional public dashboard route uses Cloudflare Access and Tunnel while keeping
Hermes loopback-only; see the public-route section in that guide before any live apply.
Fresh `sb hermes setup` also prepares the Spark/Luna/Terra/Sol routed-worker profile;
provider authentication and gateway activation remain explicit operator steps.
Hermes scheduled state is reproducible from the committed cron catalog: use
`sb hermes cron reconcile --remote NAME` to preview, then repeat with
`--confirm --force-replace`. `sb hermes health` reports false-green provider
errors, catalog drift, competing gateway owners, and dirty managed worktrees.
