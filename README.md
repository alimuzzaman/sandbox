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

## Plain Claude vs. Claude + sandbox

Claude in your IDE is already smart. It can read your code, propose
diffs, talk through architecture. What it **cannot** do alone is run
your WordPress, see what your block actually renders, query your DB,
check `debug.log`, or know that EmbedPress's `static/` is hand-written
while `assets/` is build output. It's a brilliant pair-programmer
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
  plugin's `CLAUDE.md` (text domain rules, save() BC traps, build
  conventions, FluentBoards board ID, sister-repo location) reaches
  the model on every session via `focus_get`.
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

**Fix a bug.** Take a real one: *"PDF gallery throws
`ArgumentCountError` in `category-counter.php:25` when the site is in
pt_BR"* (a FluentBoards card from a customer). Same task, two agents:

| Step | Plain Claude | Claude + sandbox |
|------|--------------|------------------|
| **1. Read the report** | Copy-paste the card content into chat. | Paste the FluentBoards short-link — agent fetches the card body via the FluentBoards REST API in one tool call. |
| **2. Reproduce** | "Let me look at the file" → reads `category-counter.php`, spots the `sprintf` + `_n()`, says "looks like a placeholder mismatch." | Literal first tool call provisions the missing piece: `fs_write` a `pt_BR.mo` with the mismatched placeholder into `wp-content/languages/plugins/`, then `visit` the page → captures the actual `ArgumentCountError` in `tail_log` as `EVIDENCE.before`. |
| **3. Find every site** | Reads the one file the report names. Misses the sibling `sub-category-counter.php` and the Pro mirror. | Grep step in `skills/fix/SKILL.md` covers every call site in one pass, AND grep the `-pro` sibling repo — catches the same pattern in three files instead of one. |
| **4. Fix** | Edit file 1, refresh, see what breaks next, edit file 2, repeat (15-25 min). | Batch-edit all three files in one pass. No fix-test-fix loop. |
| **5. Verify** | "Looks right." Maybe `php -l`. | Re-`visit` the page with the broken `pt_BR.mo` still loaded → confirm clean output → `EVIDENCE.after`. Real before/after pair against the actual failure path, not a synthetic eval. |
| **6. Report** | Prose summary. | `STATUS: FIXED` block: files changed, paired evidence rows, what was deferred, suggested branch name. |
| **7. Ship** | Commit + push + FB card update in one breath. | Stops at the working tree. Commit, push, card move — each waits for explicit "go." |

**Build a feature.** Take a real one: *"Add visitor-facing view counts
to PDF and document embeds."* Three render surfaces (Gutenberg block,
shortcode, Elementor widget), needs to read existing analytics, has
free-vs-Pro implications, and has to not break old posts. Same task,
two agents:

| Step | Plain Claude | Claude + sandbox |
|------|--------------|------------------|
| **1. Specify** | "Build a view counter on PDF embeds." Agent infers what it can, starts coding. You discover misunderstandings during review. | `load_workflow('build-feature')` → emits a Phase 1 ESTABLISH block (verb-led title, size class, success criteria that are *live-verifiable*, out-of-scope list, impact analysis, edge cases). You sign off OR redirect before any code is written. |
| **2. Plan** | Skipped. Or done in chat as prose nobody references later. | Phase 2 PLAN: **reuse audit** finds existing `embedpress_analytics_views` table (does NOT invent a new one), finds `data-embed-type` already emitted at `EmbedPressBlockRenderer.php:573`. **Cross-surface grep** catches that shortcode emits `data-embed-type="document_pdf"` (block emits `"PDF"`) and Elementor puts the attr on the iframe — discovered in Phase 2, not in Phase 3 after a broken render. |
| **3. Know your code** | Generic WP knowledge. Doesn't know `static/` is hand-written while `assets/` is build output, doesn't know which textdomain to use, doesn't know save() changes need `deprecated[]` entries. | Focused plugin's `CLAUDE.md` auto-loaded by `focus_get`. The block's "conditional emission" BC pattern is in context. The static→assets mirror rule is in context. The FluentBoards board ID is in context. |
| **4. Slice the build** | Horizontally: "first the DB, then the API, then the UI." Integration bugs surface at the end. | Phase 2 declares vertical slices. **Slice 1** = thinnest possible end-to-end: PDF block only, DB read → PHP render → frontend badge → live `visit` screenshot. **Slice 2** = extend to Document. Integration bugs surface on day 1. |
| **5. Apply non-negotiables** | You have to remember to ask: nonce? capability? prefix? Escape on output? | Workflow enforces them per-Edit: auth on every handler, sanitize-in / escape-out, prefix everything with the plugin slug, WP APIs over raw PHP. Listed in the workflow contract, applied automatically. |
| **6. Verify** | "Compiled OK, looks right." Or `php -l`. Or "tested on my machine." | Each slice: `visit` the live test post → DB row count before, badge increment, DB row count after, screenshot. Real before/after evidence, not "looks right." |
| **7. Handle mid-build redirects** | You say "wait, this should also work when analytics is off" — agent silently expands scope, you discover it later. | Same redirect → agent acknowledges, adds a separate `embedpress_show_visitor_view_count` toggle, self-record endpoint with session-dedup, re-verifies. Final SHIPPED block has a "Spec drift" section saying *what Phase 1 said / what shipped instead / why*. |
| **8. Report** | Prose summary you have to read to figure out what shipped. | STATUS: SHIPPED block: every Phase 1 success criterion gets a paired evidence row from a live MCP call. Rollout notes (toggle name, flush requirement, free/Pro gating). Draft changelog entry. Suggested branch name. |
| **9. Ship** | Commit + push in one go because the agent assumed you wanted that. | Stops at the working tree. Commit, push, FluentBoards card move — each requires explicit "go." Approval for one doesn't carry to the next. |

**What this looked like on the actual sandbox run for this exact
feature:** 7 files modified across `EmbedPress/` + `static/js/` +
`static/css/`, mirrored to `assets/`. Three render surfaces verified
live with `visit` screenshots. Mid-stream redirect to decouple from
analytics handled in a single Phase 3 follow-up. Final SHIPPED block
showed DB counts going 5→6 with the new row tagged
`source: "visitor_view_count"` proving the new self-record path. Total
elapsed: one session. No accidental commits, no scope creep past what
the user re-specified.

**Design a page.** Take a real one: *"Build a help-center landing page
with hero + 3-column doc-category grid + Pricing FAQ accordion, then
ship it."* Same task, two agents:

| Step | Plain Claude | Claude + sandbox |
|------|--------------|------------------|
| **1. Create the post** | Describes the page in chat; you create it manually in wp-admin. | `wp_rest` POST `/wp/v2/pages` creates the page in one call. Returns the post ID. |
| **2. Build the layout** | Generates a block-markup string and hopes you paste it in correctly. | Writes the block JSON via `fs_write` (or for stateful blocks/Elementor widgets, `load_skill('wp-pilot')` drives real wp-admin headlessly with auto-login so the output is byte-perfect — the only way to avoid the "Block contains unexpected content" recovery prompt). |
| **3. Use plugin blocks** | Knows the block name from training data, guesses at the attribute shape. Often wrong. | Focused plugin's `CLAUDE.md` is loaded — the BetterDocs `doc-category-grid` block's attribute names + defaults + BC rules are in context before the JSON is written. |
| **4. Verify rendering** | "Open the page in your browser and see." | `visit` returns a PNG screenshot + DOM + console errors + network failures in one call. If a CSS class is missing or an image 404s, the agent sees it without you switching tabs. |
| **5. Iterate** | "Try this CSS." (you paste it, refresh, screenshot, paste back, repeat.) | Agent edits the stylesheet via `fs_write` → re-`visit` with `--screenshot` → diffs against the previous PNG. Real iteration loop without you in the middle of every cycle. |
| **6. Ship** | Manual: copy markup into the staging site, eyeball it. | Stops at the working tree. The page exists locally; export via WXR (`runtime/seeds/`) or commit the block JSON to the plugin repo — your call, on your "go." |

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
