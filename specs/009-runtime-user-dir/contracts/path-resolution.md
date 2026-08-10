# Contract: Base & Path Resolution

The external interface of this feature is (1) one environment variable, (2) the
on-disk base layout, (3) one new CLI command, and (4) the `.mcp.json` env propagation.
These are the contracts other code/users depend on.

## C1 — Environment variable

| Name | Type | Default | Semantics |
|------|------|---------|-----------|
| `SANDBOX_HOME` | absolute or `~`/relative path | `~/sandbox` | The single base for ALL machine-state. Read identically by the `sb` CLI and the MCP server. Changing it relocates everything (after `sb migrate`/`sb home <dir>`). |

Resolution: `expanduser()` then `resolve()`. Both processes MUST produce the same value
for the same environment. An explicit `SANDBOX_HOME` always wins. After a successful
`sb home <dir>`, Sandbox records a non-secret, owner-only selection hint at
`~/.config/sandbox/home`; a later shell/MCP process uses that hint only when the variable
is absent.

## C2 — On-disk base layout (post-migration)

```
$SANDBOX_HOME/
├── runtime/
│   ├── compose/                 # generated compose files (absolute mounts)
│   ├── wp-<instance>/           # per-instance WP install (bind-mounted)
│   ├── snapshots/               # local snapshots
│   ├── dl-cache/{wp-cli,wp-http}# shared download cache
│   ├── seeds/                   # demo content / WXR
│   ├── test-suite/ test-tools/  # phpunit harness
│   ├── proxy/{certs,Caddyfile,proxy.yml}
│   ├── herd-shims/<instance>/   # herd php PATH shims
│   ├── .venv-tools/             # tools venv (recreated, never moved)
│   ├── wp-cli.phar              # shared built-in wp-cli
│   └── registry.json            # project-root → instance map (authoritative)
├── config.json                  # user-global config (was ~/.config/sandbox/config.json)
├── sandbox.local.yml            # per-machine instance blocks + secrets
└── .env.local                   # secrets (mode 600)
```

Repo checkout retains ONLY code/assets: `sb`, `sandbox/`, `mcp/wp-server/` (incl. its
`.venv`), `.cli-venv`, `config/`, `skills/`, `workflows/`, `docs/`, `sandbox.yml`.

## C3 — CLI command: `sb migrate` (and `sb home`)

```
sb migrate [--dry-run] [--force]
```

| Aspect | Contract |
|--------|----------|
| Effect | Relocate existing in-repo/`~/.config` state under `$SANDBOX_HOME`; recreate baked artifacts; verify instances boot. |
| Idempotent | Re-running after success is a no-op (exit 0, "already migrated"). |
| `--dry-run` | Print the planned moves/regenerations; change nothing. |
| Conflict | If both base and `<repo>/runtime` hold state: abort non-zero with guidance; base is authoritative; no merge. `--force` is NOT a merge — reserved for re-verify only. |
| Secrets | Move `.env.local` preserving mode 600; never print contents. |
| Auto-hook | When old state is detected and the base is empty, ordinary commands trigger the same migration once (no manual step required for the common upgrade). |

The automatic path is conservative: it stages and verifies every destination before
removing a source, holds a migration lock, and refuses any populated/different
destination instead of merging it. A journal permits a verified interrupted transfer to
resume; a separately restored legacy runtime remains a visible conflict.

```
sb home [<new-dir>]
```

| Aspect | Contract |
|--------|----------|
| No arg | Print the resolved base and whether state is present. |
| `<new-dir>` | Relocate the entire base to `<new-dir>` (move pure-data + recreate venv + regenerate compose/herd/caddy), then update the persisted override hint so future invocations resolve it. Instances MUST boot from the new base; nothing references the old base. |

(If a separate `sb home` command is judged redundant with `SANDBOX_HOME` + `sb migrate`
during implementation, the relocation behavior still MUST be reachable — e.g.
`SANDBOX_HOME=<new> sb migrate`. The contract is the behavior, not the command name.)

## C4 — `.mcp.json` env propagation

On `sb setup`, the generated/registered `sandbox` MCP server entry MUST include:

```json
{ "command": "...", "args": ["..."], "env": { "SANDBOX_HOME": "<resolved base>" } }
```

so the MCP process inherits the exact base. When absent (older registration), the MCP
server MUST default to `~/sandbox` — identical to the CLI default.

## C5 — Backward-compat fallback (read path)

For each of {config.json, sandbox.local.yml, .env.local, registry.json}: read the
base-relative location first; if absent, read the legacy location
(`~/.config/sandbox/config.json`, `<repo>/sandbox.local.yml`, `<repo>/.env.local`,
`<repo>/runtime/registry.json`). Fallback is read-only and logged once (no contents).
Fallback is removed only after migration is proven on the live stack (constitution VI).
