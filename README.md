# Sandbox

A real WordPress environment for designers, developers, and QA at WPDeveloper —
drivable by Claude Code (or any MCP client: Cursor, Cline, Continue, Zed).

**One folder.** One config file. One CLI. Everything Claude needs to design pages,
fix bugs, run migrations, query the DB, check email — without leaving this folder.

---

## Get started

```bash
git clone <this-repo> sandbox
cd sandbox
./install.sh
```

`./install.sh` walks you through it step by step: it makes sure `python3` is
present (offering to install it if not), then runs `./sb setup`. Prefer to skip
the wrapper? Just run `./sb setup` directly — it does the same thing.

`setup` checks prerequisites (Docker running, Python 3.9+), then boots Docker,
installs WordPress, generates an Application Password, builds the MCP server, and
writes `.mcp.json` inside this folder.

**Missing a prerequisite?** `setup` offers to install it for you. If `python3`,
Docker, or the `venv` module isn't found, it prompts `Install now? [y/N]` and
runs the right command for your package manager (Homebrew / apt / dnf) — no
hunting for install docs. The default is **No** (it never installs without your
yes); non-interactive runs (CI) just print the command. Docker Desktop installs
via `brew install --cask docker`; you then open it once to accept the license.
The base install needs **no `sudo` password** — WordPress comes up at
`http://localhost:8188` out of the box.

Connect external integrations on demand — each one is its own command, so
you only set up what you'll actually use:

```bash
./sb connect fb     # FluentBoards (URL + email + app password)
./sb connect gh     # GitHub org/user (auto-detects gh CLI auth)
```

Skipping these is fine — the sandbox itself runs without them. `gh` is
detected automatically: if you're already signed in with `gh auth login`,
`connect gh` just reports the existing connection and saves the username.

Saved to the gitignored `sandbox.local.yml` (+ mirrored to `.env.local`).

That's it. `./sb setup` registered the `sandbox` MCP server at user
scope (`~/.claude.json`), so **every** `claude` session on the machine
now has it — regardless of which directory you launch from:

```bash
claude          # in any project, in any dir
```

If you run more than one WordPress instance (see `./sb instance create`),
each instance gets its **own** MCP server: `sandbox` for `main`,
`sandbox-<name>` for the rest. A Claude session targets an instance by
calling that server's tools (`mcp__sandbox-<name>__*`), so two concurrent
sessions can work on different instances without their focus / active-project
state colliding. `./sb instances` prints the instance→server mapping.

Each instance can also run a different **web server** —
`./sb instance create ngx --server nginx` (or `--server litespeed`, default
`apache`). Useful for testing caching/permalink behavior across Apache, nginx,
and OpenLiteSpeed. `./sb instances` shows each instance's server.

### Clean URLs — `https://<name>.sb`

By default instances serve at `http://localhost:<port>`. You can upgrade to a
**trusted, no-port HTTPS URL** — `https://blog.sb`, `https://main.sb` — with one
optional setup:

```bash
./sb domains setup     # one-time, asks your password ONCE
```

This installs a local certificate authority (via [mkcert](https://github.com/FiloSottile/mkcert)),
wires `*.sb` resolution, and starts a small reverse proxy. It then gives **every**
instance — including `main` — a `<name>.sb` domain with a trusted certificate, so
nothing stays on `localhost`. After this, **every new instance gets its clean
HTTPS URL automatically, with no further password.**

You don't have to run it up front: the **first time** you create an instance,
`./sb instance create blog` offers to enable HTTPS right then (`Enable trusted
https://blog.sb? [y/N]`). Say no and it just uses a port (and won't ask again);
say yes once and you're set for good. It coexists with Laravel Valet (binds a
separate loopback IP, so your `.dev`/`.test` sites are untouched). Undo anytime
with `./sb domains teardown`.

For a live view, **`./sb dashboard`** opens an interactive full-screen TUI of
all instances with keys to start/stop/restart, open in browser, set focus, and
create/delete — auto-refreshing status every couple of seconds.

Prefer a browser? **`./sb web`** serves the same dashboard as a local web page
(`http://127.0.0.1:8765`, localhost only, no extra deps) — instance cards with
live status, links, start/stop/restart/focus controls, a guided "New instance"
form (name → server → plugins → content/options), and a built-in terminal. It
has real navigable URLs (`/instance/<name>`, `/usage`) so pages are
back/forward- and deep-link-friendly. The UI is authored in TypeScript under
`src/web` and built to a vendored bundle (`config/sandbox-web.js`) that `sb`
inlines — running `./sb web` needs no Node; only rebuilding does
(`scripts/build-web-js.sh`).

**Activation phrase: `focus <plugin>`.** Just say it in chat —
"focus betterdocs", "focus embedpress", "work on xspeed" — and the
agent runs the handshake automatically: persists the focus, loads
the full sandbox `CLAUDE.md`, fetches the plugin's own conventions
and available skills. After that the session is in sandbox mode for
that plugin until you close it.

The 2KB operating summary ships on every session via the MCP
`instructions` field, so the agent already knows the activation
trigger and the core reflexes (live repro before any fix, MCP tools
over raw bash, snapshot before destructive DB ops, never commit/push
without your word). Deeper context is loaded on demand via
`load_context` and `load_skill(name)`.

You can also invoke any skill as a slash command:

```
/mcp__sandbox__focus <plugin>          # explicit activation handshake
/mcp__sandbox__activate                # load full sandbox operating guide
/mcp__sandbox__fix <task>              # engage the one-pass bug-fix loop
/mcp__sandbox__build_feature <task>    # three-phase feature workflow (establish → plan → build)
/mcp__sandbox__bug_repro               # reproduce a bug live
/mcp__sandbox__wp_pilot                # headless wp-admin authoring
/mcp__sandbox__snapshot                # snapshot/restore guidance
/mcp__sandbox__wp_debug                # debugging the WP stack
```

Claude now has 20 tools wired to your local WordPress (`wp_cli`,
`wp_rest`, `http_fetch`, `db_query`, `tail_log`, `visit` (headless
Chromium with auto-login on `/wp-admin/`), `fs_read/write/list`,
`mail_list/get`, `focus_get/set`, `load_context`, `load_skill`,
`load_workflow`, and more).

---

## Plain Claude vs. Claude + sandbox

Claude in your IDE is already smart. It can read your code, propose
diffs, talk through architecture. What it **cannot** do alone is run
your WordPress, see what your block actually renders, query your DB,
check `debug.log`, or know your plugin's specific conventions
(build pipeline, textdomain rules, BC traps, where the source folder
lives vs. the build output). It's a brilliant pair-programmer
working blindfolded against an unfamiliar codebase.

The sandbox removes the blindfold and hands it the keys.

### What plain Claude has

- Your source code on disk (Read / Write / Edit).
- The internet (web search, fetch).
- Its training knowledge of WordPress / PHP / JS.
- Nothing about *your* WordPress, *your* plugin's conventions, or
  whether the edit it just made actually works.

### What Claude + sandbox has, on top of that

- **A live WordPress at `http://localhost:8188`** with your plugins
  symlinked in. Edits land in seconds, no rebuild.
- **20 MCP tools** to drive it: `wp_cli`, `wp_rest`, `db_query`,
  `tail_log`, `visit` (headless Chromium, auto-login on wp-admin),
  `http_fetch`, `fs_read/write`, `mail_list/get`, etc. The agent acts
  on your stack instead of guessing at it.
- **Your plugin's institutional knowledge** auto-loaded. The focused
  plugin's `CLAUDE.md` (textdomain rules, save() BC traps, build
  conventions, task-tracker board, sister-repo location) reaches the
  model on every session via `focus_get`.
- **A 2KB operating prompt** in every Claude session via the MCP
  `instructions` field — reflexes ("first tool call reproduces, not
  Read"), anti-patterns ("declaring fixed from code reading"),
  workflow triggers ("focus &lt;plugin&gt;" → handshake).
- **Skills + workflows** for the patterns that repeat: `fix` for
  bugs (one-pass loop with paired before/after evidence),
  `build-feature` for new features (three-phase: establish → plan →
  build with size-scaled gates), `wp-pilot` for editor-stateful
  authoring, `fluentboards` for task management.

### What that means on three tasks you actually do

Same task, two agents — what changes, step by step.

**Fix a bug in your plugin.** A customer reports something breaks
under a specific condition.

| Step | Plain Claude | Claude + sandbox |
|------|--------------|------------------|
| **1. Understand the report** | Asks you what version, what other plugins are active, what theme. Reads the report's file paths. | Your plugin's `CLAUDE.md` is already in context (textdomain rules, BC traps, source layout). Can fetch the task-tracker card body via REST in one tool call. |
| **2. Reproduce** | "Let me look at the file" → reads the code, spots the suspect line, says "looks like X is the cause." Can't actually verify. | First tool call provisions whatever the bug needs to fire (a translation file, a missing row, a setting flip) and triggers it on the live WP. Captures the real error in the log as `EVIDENCE.before`. |
| **3. Find every affected site** | Reads the file the report names. Misses the sibling implementation and the Pro-side mirror. | Greps every call site across the focused plugin AND its `-pro` sibling in one pass. Same pattern caught wherever it lives. |
| **4. Fix** | Edits file 1, asks you to test, edits file 2, asks again. 15-25 min per round, 3-5 rounds. | Batch-edits every affected file in one pass. No fix-test-fix loop. |
| **5. Verify** | "Looks right." Or `php -l`. Or "tested on my machine." | Re-triggers the exact same failing call from step 2 — confirms output flipped → `EVIDENCE.after`. Real before/after pair against the actual failure path. |
| **6. Report** | Prose summary you have to parse to figure out what shipped. | `STATUS: FIXED` block: files changed, paired evidence rows, what was deferred, suggested branch name. |
| **7. Ship** | Commit + push + task-tracker update in one breath, because the agent assumed you wanted that. | Stops at the working tree. Commit, push, card update — each waits for explicit "go." |

**Build a new feature in your plugin.** A founder request or a
task-tracker card asking for net-new functionality.

| Step | Plain Claude | Claude + sandbox |
|------|--------------|------------------|
| **1. Specify** | Agent infers what it can from the request and starts coding. Misunderstandings surface during review. | `load_workflow('build-feature')` → Phase 1 ESTABLISH block: verb-led title, size class, live-verifiable success criteria, out-of-scope list, impact analysis, edge cases. You sign off or redirect *before* any code is written. |
| **2. Plan** | Skipped. Or done in chat as prose nobody references later. | Phase 2 PLAN: **reuse audit** names every existing helper / table / REST route / hook the feature will ride on instead of reinventing. **Cross-surface grep** catches discrepancies between render paths in advance. |
| **3. Know your code** | Generic WP knowledge. Doesn't know your plugin's build pipeline, textdomain rules, or BC patterns. | Focused plugin's `CLAUDE.md` auto-loaded by `focus_get`. Source layout, build commands, BC patterns, task-tracker board — all in context before any code is read. |
| **4. Slice the build** | Horizontally: "first the DB, then the API, then the UI." Integration bugs surface at the end. | Vertical slices: thinnest possible end-to-end through every layer first, then thicken. Integration bugs surface on day 1. |
| **5. Apply non-negotiables** | You have to remember to ask: nonce? capability? plugin-slug prefix? Escape on output? | Workflow enforces them per-Edit: auth on every handler, sanitize-in / escape-out, prefix everything with the plugin slug, WP APIs over raw PHP. Listed in the contract, applied automatically. |
| **6. Verify** | "Compiled OK, looks right." Or `php -l`. Or "tested on my machine." | Each slice: trigger via the right MCP tool → capture output → compare against that slice's success criterion. Real evidence per slice, not "looks right" once at the end. |
| **7. Handle mid-build redirects** | You say "wait, also handle X" — agent silently expands scope. You discover later. | Same redirect → agent acknowledges, builds the addition, re-verifies. Final SHIPPED block carries a **Spec drift** section: *what Phase 1 said / what shipped instead / why*. |
| **8. Report** | Prose summary you have to read to figure out what shipped. | `STATUS: SHIPPED` block: every Phase 1 success criterion gets a paired evidence row from a live MCP call. Rollout notes (toggle + default, flush requirement, free/Pro gating). Draft changelog entry. Suggested branch name. |
| **9. Ship** | Commit + push in one go because the agent assumed you wanted that. | Stops at the working tree. Commit, push, task-tracker move — each requires explicit "go." |

**Design a page in WordPress.** A landing page, a help-center index,
a marketing page — anything you'd normally build by hand in wp-admin.

| Step | Plain Claude | Claude + sandbox |
|------|--------------|------------------|
| **1. Create the page** | Describes it in chat; you create it manually in wp-admin. | `wp_rest` POST creates the page in one call and returns the post ID. |
| **2. Build the layout** | Generates a block-markup string and hopes you paste it in correctly. | Writes block JSON directly via `fs_write`. For stateful blocks / Elementor widgets, `load_skill('wp-pilot')` drives real wp-admin headlessly with auto-login so the editor output is byte-perfect. |
| **3. Use your plugin's blocks** | Knows the block name from training data, guesses at attribute shape. Often wrong. | Focused plugin's `CLAUDE.md` is loaded — block attribute names + defaults + BC rules are in context before the JSON is written. |
| **4. Verify rendering** | "Open the page in your browser and see." | `visit` returns a PNG screenshot + DOM + console errors + network failures in one call. If a CSS class is missing or an image 404s, the agent sees it without you switching tabs. |
| **5. Iterate** | "Try this CSS." You paste, refresh, screenshot, paste back, repeat. | Agent edits the stylesheet → re-`visit` with `--screenshot` → diffs against the previous PNG. You're not in the middle of every cycle. |
| **6. Ship** | Manual: copy markup into staging, eyeball it. | Stops at the working tree. Export via WXR or commit the block JSON — your call, on your "go." |

### The two underlying patterns

1. **Live evidence is the only evidence.** Plain Claude can't reach
   your stack, so it ships claims. Sandbox-Claude can reach your stack,
   so it ships evidence. Every "fixed" / "shipped" / "verified" is
   backed by an MCP call against the running WordPress.

2. **The work stops at the working tree.** Commits, pushes, FB card
   updates, PR creations — all wait for explicit user confirmation.
   The sandbox makes Claude powerful; the gates make sure that power
   doesn't outrun your intent.

Net effect: Claude's intelligence stays the same; its **leverage on
your codebase** changes by an order of magnitude. The same model that
guessed wrong yesterday can ship a verified three-surface feature today
because it has the stack, the conventions, and the tools.

---

## Daily commands

```bash
./sb pick                 # interactive checklist of WPDeveloper plugins
./sb use <project>        # activate a profile (embedpress, design-elementor, …)
./sb add <repo>           # clone + link a single plugin from GitHub
./sb update               # git pull every plugin in the active project
./sb focus <plugin>       # tell Claude which plugin is the active one
./sb open [admin|site|mail]  # open in browser (default: admin)
./sb visit <url> [...]    # load URL in headless Chromium, report DOM/console/iframes as JSON
./sb snapshot <name>      # save DB + uploads (for fast bug repro / QA)
./sb restore <name>       # restore a saved snapshot
./sb snapshots            # list saved snapshots
./sb xdebug on|off        # toggle step-debug (port 9003, host trigger)
./sb doctor               # audit the stack — runs after setup, run anytime
./sb connect <fb|gh>      # save FluentBoards or GitHub creds
./sb status               # which containers + project + focus are active
./sb down                 # stop containers (state is preserved)
./sb clean                # stop + wipe DB volume (start fresh)
```

Run `./sb` with no args for the full list.

### Working on a plugin

```bash
./sb use embedpress                  # the profile bundles embedpress + deps
./sb add wpdeveloper/embedpress-pro  # clone + link Pro repo too
./sb focus embedpress-pro            # Claude defaults to this repo
```

`add` accepts `org/repo`, full HTTPS URL, SSH URL, or bare `repo` (if you set
`defaults.github_org` in `sandbox.yml`). It clones into `./plugins/`, symlinks
into the running WordPress, activates it, and persists into `sandbox.local.yml`.

---

## What Claude can do

After `setup`, Claude has these 20 MCP tools (auto-registered at user
scope so they're available in every Claude session on the machine):

| Tool | Purpose |
|------|---------|
| `wp_cli` | Run any `wp` command |
| `wp_exec` | Arbitrary shell in any container (composer, npm, php, …) |
| `wp_rest` | Call the WordPress REST API (uses pre-wired app password) |
| `http_fetch` | Lightweight anonymous HTTP probe — status, headers, body, redirects. Right tool when `wp_rest` is wrong (no auth wanted) and `visit` is overkill (no browser needed) |
| `visit` | Headless Chromium. Auto-logs in on `/wp-admin/` URLs using pre-wired admin creds. Returns status + title + iframes + console + network + optional screenshot |
| `db_query` | Run SQL — writes require `mutate: true` |
| `tail_log` | Tail `wp-content/debug.log` |
| `fs_read` / `fs_write` / `fs_list` | Read/write files under `runtime/wp/` |
| `mail_list` / `mail_get` | Read Mailpit (test SMTP inbox) |
| `focus_get` / `focus_set` | Focus on a plugin. `focus_set` auto-switches the active project to one that contains the plugin |
| `activate_plugin` / `deactivate_plugin` | Toggle plugins |
| `import_content` | Import a WXR XML from `runtime/seeds/` |
| `load_context` | Pull the full sandbox `CLAUDE.md` into the conversation on demand (the 2KB summary ships automatically) |
| `load_skill` | Pull a sandbox skill (`fix`, `bug-repro`, `snapshot`, `wp-debug`, `wp-pilot`, `fluentboards`) on demand |
| `load_workflow` | Pull a sandbox workflow (`build-feature`) on demand |

Plus Claude's normal `Read`/`Write`/`Edit` reach the plugin source on disk —
because the source is bind-mounted into the WP container, edits are live with
no rebuild.

---

## Bringing your own skills, CLAUDE.md, and configs

Three attach points, all automatic:

1. **Folder-level CLAUDE.md** — drop a `CLAUDE.md` at the sandbox root. Claude
   Code auto-loads it for every conversation started here.

2. **Plugin-level CLAUDE.md** — if a plugin repo you `add`ed has its own
   `CLAUDE.md`, `./sb focus <slug>` makes Claude pull it in via
   `focus_get`. Your plugin docs travel with the plugin.

3. **Personal skills** — `~/.claude/skills/*.md` are loaded by Claude Code
   itself. They work alongside the sandbox without conflict.

### Skills + workflows shipping today

**Skills** (single-purpose reflexes, loaded via `load_skill('<name>')` or
the matching `/mcp__sandbox__<name>` slash command):

| Skill | Purpose |
|-------|---------|
| `fix` | One-pass bug-fix loop (reproduce live → read all call sites → batch edit → verify) |
| `bug-repro` | Canonical reproduction loop on the live stack |
| `snapshot` | DB + uploads snapshot/restore guidance |
| `wp-debug` | Diagnosing WP / plugin errors (tail_log, Xdebug, Query Monitor, symptom→cause table) |
| `wp-pilot` | Headless wp-admin authoring (Gutenberg blocks with stateful `save()`, Elementor widgets) |
| `fluentboards` | Read/update FluentBoards tasks via REST |

**Workflows** (multi-phase playbooks with user gates, loaded via
`load_workflow('<name>')` or `/mcp__sandbox__<name>`):

| Workflow | Purpose |
|----------|---------|
| `build-feature` | Three-phase feature build: ESTABLISH (spec + impact + edge cases) → PLAN (file plan + reuse audit + slicing + rollout) → BUILD (slice by slice with live verification). Size-scaled gates (S=0, M=1, L=2) |

### Adding a new skill or workflow

One folder per skill / workflow, named after itself, with a single canonical
entry file. Supporting assets (examples, screenshots, helper scripts) live
alongside it in the same folder.

```
skills/
└── my-new-skill/
    ├── SKILL.md              # the entry file — required, uppercase
    ├── examples/             # optional supporting files
    └── notes.md              # optional supporting files

workflows/
└── my-new-workflow/
    └── WORKFLOW.md           # required, uppercase
```

Reference one from another by its full path:
`workflows/build-feature/WORKFLOW.md`, `skills/fix/SKILL.md`.

---

## Customizing

All knobs live in [sandbox.yml](sandbox.yml). Edit, then re-run setup:

```bash
nano sandbox.yml          # change ports, admin creds, projects, etc.
./sb setup        # idempotent — applies only what changed
```

Per-machine overrides (not committed) go in `sandbox.local.yml`. Things to
override there:

```yaml
defaults:
  plugins_home: "$HOME/dev"     # use your existing plugin clones instead of ./plugins
  github_org: "wpdeveloper"     # so `./sb add embedpress` resolves
```

To add a new project, copy a `projects:` block in `sandbox.yml`, change the
slug + source, save, run `./sb use <new-name>`.

---

## What lives where (everything stays inside the folder)

```
sandbox/
├── sb                      # the CLI (Python script — invoke as ./sb)
├── sandbox.yml             # single source of truth — edit this
├── sandbox.local.yml       # per-machine overrides (gitignored)
├── .mcp.json               # auto-generated — Claude Code reads this
├── docker-compose.yml      # managed by the CLI
├── runtime/
│   ├── wp/                 # WordPress install (bind-mounted into the container)
│   ├── seeds/              # demo content / Elementor JSON / WXR imports
│   └── snapshots/          # gitignored — DB + uploads snapshots from ./sb snapshot
├── plugins/                # default home for cloned plugin repos (gitignored)
├── mcp/wp-server/          # the Python MCP server + its venv
├── tools/visit/            # headless-browser runner invoked by the visit MCP tool
├── skills/
│   ├── fix/SKILL.md            # one-pass bug-fix loop
│   ├── bug-repro/SKILL.md      # canonical live reproduction loop
│   ├── snapshot/SKILL.md       # snapshot/restore guidance
│   ├── wp-debug/SKILL.md       # diagnosing WP/plugin errors
│   ├── wp-pilot/SKILL.md       # headless wp-admin authoring
│   └── fluentboards/SKILL.md   # FluentBoards REST integration
├── workflows/
│   └── build-feature/WORKFLOW.md   # three-phase feature build (establish → plan → build)
├── memory/
│   ├── plugin-behavior/    # tracked — cross-plugin runtime findings
│   ├── feature-history/    # tracked — retros from shipped features
│   └── repros/             # gitignored — per-bug repro state
└── .claude/
    └── skills/             # symlink → ../skills (Claude Code's native skill discovery)
```

The only state outside this folder: Docker's named volume `db_data` (cleared by
`./sb clean`).

---

## Troubleshooting

```bash
./sb doctor       # checks containers, WP, REST auth, MCP venv, symlinks, focus
```

Every failure prints a `→ hint` next to it. Common ones:

- **REST auth fails** — re-run `./sb setup` (regenerates the app password)
- **MCP server not connected in Claude Code** — run `claude mcp list` and
  confirm `sandbox` (+ any `sandbox-<instance>`) is `✓ Connected`. If
  missing, re-run `./sb setup` (it re-registers every instance's user-scope
  server). For project-local fallback, `cat .mcp.json` and verify
  `./mcp/wp-server/.venv/bin/python` is the Python path it references
- **A session keeps landing on the wrong instance** — you're calling the
  wrong tool namespace. `mcp__sandbox__*` = `main`; use `mcp__sandbox-<name>__*`
  for instance `<name>`. `./sb instances` shows the mapping. A freshly
  created instance's server may need a `claude` restart to appear
- **Sandbox prompt / reflexes not engaging** — the 2KB summary ships via
  the MCP `instructions` field on every session. If the model isn't
  picking up sandbox behavior, verify the server is connected (see
  above) and try invoking `/mcp__sandbox__activate` to explicitly load
  the full CLAUDE.md
- **Container won't start** — `./sb down && ./sb setup`
- **Want a fresh start** — `./sb clean && ./sb setup`

For everything else, ask Claude — it has `tail_log`, `wp_exec`, and `db_query`
and can usually diagnose itself.

---

## Roadmap

- **Shipped** — Docker WP stack + the `sandbox` MCP server with 20 tools,
  auto-registered at user scope; headless Chromium with auto-login (`visit`);
  size-scaled feature workflow (`build-feature`); one-pass bug-fix skill
  (`fix`) with provision-the-repro branch and cross-surface guidance;
  FluentBoards integration; focus auto-link (`./sb focus <plugin>` switches
  the active project to one that contains the plugin); 2KB MCP instructions
  field so the operating prompt + reflexes ship to every Claude session
  without launch-flag rituals.
- **In progress** — workflow sharpening as it gets stress-tested on real
  features and bugs; per-plugin scaffolding skills (`add-block`,
  `add-shortcode`, etc.) maturing in the plugin repos themselves.
- **Next** — remote API surface so the sandbox can be triggered from outside
  the dev's machine (phone, Slack, FluentBoards webhook); `figma-mcp` so
  designers can pull from Figma straight into a WordPress page.

Everything plugs into the same `sandbox.yml`. Re-run `./sb setup` after any
config change — it's idempotent.
