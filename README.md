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
./wp-sandbox setup
```

That's it. `setup` boots Docker, installs WordPress, generates an Application
Password, builds the MCP server, and writes `.mcp.json` inside this folder.

Then open Claude Code in this folder:

```bash
claude          # or: open the sandbox/ folder in your IDE → /mcp shows wp-sandbox connected
```

Claude now has 15 tools wired to your local WordPress.

---

## Daily commands

```bash
./wp-sandbox use <project>        # activate a profile (embedpress, design-elementor, …)
./wp-sandbox add <repo>           # clone + link a plugin from GitHub
./wp-sandbox update               # git pull every plugin in the active project
./wp-sandbox focus <plugin>       # tell Claude which plugin is the active one
./wp-sandbox open [admin|site|mail]  # open in browser (default: admin)
./wp-sandbox snapshot <name>      # save DB + uploads (for fast bug repro / QA)
./wp-sandbox restore <name>       # restore a saved snapshot
./wp-sandbox snapshots            # list saved snapshots
./wp-sandbox xdebug on|off        # toggle step-debug (port 9003, host trigger)
./wp-sandbox doctor               # audit the stack — runs after setup, run anytime
./wp-sandbox status               # which containers + project + focus are active
./wp-sandbox down                 # stop containers (state is preserved)
./wp-sandbox clean                # stop + wipe DB volume (start fresh)
```

Run `./wp-sandbox` with no args for the full list.

### Working on a plugin

```bash
./wp-sandbox use embedpress                  # the profile bundles embedpress + deps
./wp-sandbox add wpdeveloper/embedpress-pro  # clone + link Pro repo too
./wp-sandbox focus embedpress-pro            # Claude defaults to this repo
```

`add` accepts `org/repo`, full HTTPS URL, SSH URL, or bare `repo` (if you set
`defaults.github_org` in `sandbox.yml`). It clones into `./plugins/`, symlinks
into the running WordPress, activates it, and persists into `sandbox.local.yml`.

---

## What Claude can do

After `setup`, Claude has these tools (via MCP):

| Tool | Purpose |
|------|---------|
| `wp_cli` | Run any `wp` command |
| `wp_exec` | Arbitrary shell in any container (composer, npm, php, …) |
| `wp_rest` | Call the WordPress REST API |
| `db_query` | Run SQL — writes require `mutate: true` |
| `tail_log` | Tail `wp-content/debug.log` |
| `fs_read` / `fs_write` / `fs_list` | Read/write files under `runtime/wp/` |
| `mail_list` / `mail_get` | Read Mailpit (test SMTP inbox) |
| `focus_get` / `focus_set` | Which plugin Claude is currently working on |
| `activate_plugin` / `deactivate_plugin` | Toggle plugins |
| `import_content` | Import a WXR XML from `runtime/seeds/` |

Plus Claude's normal `Read`/`Write`/`Edit` reach the plugin source on disk —
because the source is bind-mounted into the WP container, edits are live with
no rebuild.

---

## Bringing your own skills, CLAUDE.md, and configs

Three attach points, all automatic:

1. **Folder-level CLAUDE.md** — drop a `CLAUDE.md` at the sandbox root. Claude
   Code auto-loads it for every conversation started here.

2. **Plugin-level CLAUDE.md** — if a plugin repo you `add`ed has its own
   `CLAUDE.md`, `./wp-sandbox focus <slug>` makes Claude pull it in via
   `focus_get`. Your plugin docs travel with the plugin.

3. **Personal skills** — `~/.claude/skills/*.md` are loaded by Claude Code
   itself. They work alongside the sandbox without conflict.

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
`workflows/ship-fix/WORKFLOW.md`, `skills/designer/SKILL.md`.

---

## Customizing

All knobs live in [sandbox.yml](sandbox.yml). Edit, then re-run setup:

```bash
nano sandbox.yml          # change ports, admin creds, projects, etc.
./wp-sandbox setup        # idempotent — applies only what changed
```

Per-machine overrides (not committed) go in `sandbox.local.yml`. Things to
override there:

```yaml
defaults:
  plugins_home: "$HOME/dev"     # use your existing plugin clones instead of ./plugins
  github_org: "wpdeveloper"     # so `./wp-sandbox add embedpress` resolves
```

To add a new project, copy a `projects:` block in `sandbox.yml`, change the
slug + source, save, run `./wp-sandbox use <new-name>`.

---

## What lives where (everything stays inside the folder)

```
sandbox/
├── wp-sandbox              # the CLI (run as ./wp-sandbox)
├── sandbox.yml             # single source of truth — edit this
├── sandbox.local.yml       # per-machine overrides (gitignored)
├── .mcp.json               # auto-generated — Claude Code reads this
├── docker-compose.yml      # managed by the CLI
├── runtime/
│   ├── wp/                 # WordPress install (bind-mounted into the container)
│   ├── plugins/            # symlinks to plugin source repos
│   └── seeds/              # demo content / Elementor JSON / WXR imports
├── plugins/                # default home for cloned plugin repos
├── mcp/wp-server/          # the Python MCP server + its venv
├── skills/
│   └── <name>/SKILL.md     # role packs Claude reads — designer, developer, support
├── workflows/
│   └── <name>/WORKFLOW.md  # playbooks — design-page, reproduce-ticket, ship-fix
└── memory/                 # bug history, plugin notes (grown over time)
```

The only state outside this folder: Docker's named volume `db_data` (cleared by
`./wp-sandbox clean`).

---

## Troubleshooting

```bash
./wp-sandbox doctor       # checks containers, WP, REST auth, MCP venv, symlinks, focus
```

Every failure prints a `→ hint` next to it. Common ones:

- **REST auth fails** — re-run `./wp-sandbox setup` (regenerates the app password)
- **MCP server not connected in Claude Code** — make sure you ran `claude` from
  inside this folder; check `cat .mcp.json` exists and `./mcp/wp-server/.venv/bin/python`
  is the Python path it references
- **Container won't start** — `./wp-sandbox down && ./wp-sandbox setup`
- **Want a fresh start** — `./wp-sandbox clean && ./wp-sandbox setup`

For everything else, ask Claude — it has `tail_log`, `wp_exec`, and `db_query`
and can usually diagnose itself.

---

## Roadmap

- **Now (Phase 1)** — Docker WP stack + `wp-mcp` with 15 tools, drivable by any
  MCP client. CLI subcommands: `setup`, `up`, `down`, `install`, `use`, `add`,
  `focus`, `doctor`, `projects`, `wp`, `seed`, `mcp-install`, `claude`,
  `status`, `logs`, `shell`, `clean`.
- **Phase 2** — `browser-mcp` (Playwright). Visual verification + QA recording.
- **Phase 3** — `figma-mcp` so designers can pull from Figma straight into a
  WordPress page.

Both later phases plug into the same `sandbox.yml`. Flip a toggle, re-run
`./wp-sandbox setup`.
