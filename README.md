# Sandbox

A real WordPress environment for designers, developers, and QA at WPDeveloper —
drivable by Claude Code (or any MCP client: Cursor, Cline, Continue, Zed).

**One folder.** One config file. One CLI. Everything Claude needs to design pages,
fix bugs, run migrations, query the DB, check email — without leaving this folder.

---

## Get started (3 commands)

```bash
git clone <this-repo> sandbox
cd sandbox
./sb setup
```

`setup` is non-interactive. It checks prerequisites (Docker running, Python
3.9+), then boots Docker, installs WordPress, generates an Application
Password, builds the MCP server, and writes `.mcp.json` inside this folder.

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

## Before vs. After — what changes when Claude has the sandbox

The sandbox isn't just a dev environment; it's a contract that makes
Claude operate like a senior engineer instead of a search bot. Concrete
comparison of the three workflows you'll hit daily:

### Fixing a bug from a FluentBoards card

| | **Before** (Claude without the sandbox) | **After** (Claude + sandbox) |
|---|---|---|
| **Read the card** | Manually copy-paste card into chat | `focus <plugin>` then paste card URL — agent fetches via FluentBoards API |
| **Reproduce** | "Let me look at the file" → reads code, guesses | Literal first tool call is `wp_cli` / `wp_rest` / `visit` / `tail_log` on the live stack. Captures the actual error as `EVIDENCE.before` |
| **Can't reproduce?** | Pivot to code reading, ship a guess | Provision the missing piece (`fs_write` a `.mo`, `db_query` a missing row, `wp_cli plugin install`), retry. If genuinely impossible: `STATUS: BLOCKED` with the exact missing input |
| **Fix the code** | Edit one file, refresh browser, see what breaks next, edit another, repeat (15-25 min) | Read all call sites in one pass, batch-edit, BC traps handled (deprecated[], conditional emission) |
| **Verify** | "Looks right on my machine" | Re-run the exact MCP call from repro → confirm output flipped. Paired `EVIDENCE.before` + `EVIDENCE.after` |
| **Report** | Prose summary, hope it's right | `STATUS: FIXED` block: files, evidence, what was deferred, what the human does next |
| **Ship** | Commit + push in the same breath | Stops at the working tree. Commit, push, FB card update — each waits for explicit "go" |

### Building a new feature

| | **Before** | **After** |
|---|---|---|
| **Specify** | "Just build it" — agent and human have different mental models | Phase 1 ESTABLISH block: verb-led title, success criteria (live-verifiable), out-of-scope, impact, edge cases. User signs off (Size M) or auto-proceeds (Size S) |
| **Plan** | Skipped. Agent starts editing the first file it finds | Phase 2 PLAN: reuse audit (rides on existing helpers), file-level plan, vertical slicing, BC strategy, rollout (toggle + version gate + free/Pro split + draft changelog) |
| **Build** | Horizontal slicing — "first the DB, then the API, then the UI." Integration bugs surface day 14 | Vertical slicing — first slice cuts through every layer for the simplest case. Integration bugs surface day 1 |
| **Cross-surface** | Block surface works, shortcode quietly broken | Phase 2 grep covers every render path. Block + shortcode + Elementor checked in the reuse audit |
| **Scope creep** | Founder says "also add X" mid-build, agent silently expands | Mid-stream redirects acknowledged; final SHIPPED block has a "Spec drift" section reconciling what changed vs. Phase 1 |
| **Ship** | Done = "I think it works" | `STATUS: SHIPPED` block with one row of evidence per success criterion + one per edge case, all from live MCP calls |

### Designing a page in WordPress

| | **Before** | **After** |
|---|---|---|
| **Where you work** | Local WP + a separate Figma window + a separate code editor + a terminal | One folder. Claude has `wp_rest` to create pages, `visit` to render them headless, `fs_read/write` to drop block JSON / Elementor data, `mail_list` to verify form sends |
| **Verify rendering** | "Switch to the browser and look" | `visit` with `--screenshot` returns a PNG + DOM + console + network. Real before/after when you change CSS |
| **Editor-stateful authoring** (Gutenberg blocks with stateful `save()`, Elementor widgets) | Hand-write HTML, hope it doesn't trigger Gutenberg recovery prompt | `load_skill('wp-pilot')` — drive real wp-admin via headless Playwright with auto-login. Byte-perfect editor output |

### The two patterns underneath all three

1. **Live evidence is the only evidence.** Code reading is for *understanding* — never for *deciding* something is done. Every "fixed" / "shipped" / "verified" claim is backed by an MCP call against the running stack.

2. **The work stops at the working tree.** Commits, pushes, FB card updates, PR creations — all wait for explicit user confirmation. Approval for one doesn't carry to the next.

The throughput delta is real: bug fixes that used to be 20-25 minutes per round (× 3-5 rounds) drop to 5-10 minutes total. Feature work that used to scope-creep for a week ships in a day with the same surface area covered.

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
  confirm `sandbox` is `✓ Connected`. If missing, re-run `./sb setup`
  (it re-registers the user-scope server). For project-local fallback,
  `cat .mcp.json` and verify `./mcp/wp-server/.venv/bin/python` is the
  Python path it references
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
