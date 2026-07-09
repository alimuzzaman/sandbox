# Remote VPS hosting for sandbox instances

## 1. What this is

`./sb remote` + `./sb deploy` let you run a sandbox instance on a VPS you already own
and manage, instead of on your local machine — reached over Tailscale, with the exact
same CLI/MCP surface as a local instance. This is a first-class capability
(`specs/014-remote-vps-hosting/`), grounded in a deeper feasibility study at
`docs/remote-hosting-prd.md` (read that doc's §0 for the resolved architecture
decisions this feature encodes).

**Not this**: a continuous file-sync daemon, an on-demand-power-managed VPS, or a
multi-tenant/shared-VPS story. See §5 for what's explicitly out of scope.

## 2. The model in one sentence

**Co-location, not remote-control.** The MCP server, `sb`, `$SANDBOX_HOME`, Docker, and
all containers move onto the VPS together — you reach them over a Tailscale mesh through
a SECOND, separately registered MCP server (`sandbox-<remote-name>`), not by adding a
`--remote` flag to your existing local tools. Your local `sandbox` MCP server and all
your local instances are completely unaffected.

## 3. First-time setup

```bash
./sb remote add myvps ssh://ubuntu@203.0.113.10
./sb remote provision myvps
```

`provision` SSHes in and, non-interactively, installs Tailscale (joining the tailnet if
`TAILSCALE_AUTHKEY` is set in your shell environment before running provision — otherwise
it installs the package and you `tailscale up` on the VPS manually once, then re-run
provision), Docker CE + compose plugin, the `sb` runtime itself, and the `visit` tools
venv (Playwright + headless Chromium — needed server-side, since `visit` must reach
`localhost:<port>` and the VPS's own `.tst` proxy). It then starts the remote MCP server
(streamable-HTTP, bound ONLY to the VPS's Tailscale interface — never `0.0.0.0`) and
prints the Tailscale address + port to register.

Register the second MCP server in Claude Code:

```bash
claude mcp add --scope user --transport http sandbox-myvps \
  http://<tailscale-ip-printed-above>:9174 \
  --header "Authorization: Bearer <token-printed-above>"
```

(Exact `claude mcp add` flags may vary by Claude Code version — check `claude mcp add
--help`.) Your local `sandbox` MCP server registration is completely untouched.

## 4. Deploying code

```bash
cd ~/some/plugin/project
./sb deploy --remote myvps
```

This is a **one-way, on-demand** push — never a continuous sync. Every deploy:

1. `git push`es your current `HEAD` to a deploy-target git repo on the VPS (works even
   for a branch never pushed to GitHub/origin — it's a direct git-to-git push over your
   existing SSH connection).
2. Resets the VPS's working tree to that commit.
3. Applies your CURRENT uncommitted changes on top — both edits to tracked files and
   brand-new untracked files. This step REPLACES whatever a previous deploy applied; it
   never stacks. "Is my code live on the VPS" always has one answer: "as of my last
   `./sb deploy`."

There is no project-level config for this — you always pass `--remote <name>`
explicitly (unlike `plugin-check`'s slug, there's no single obviously-correct default
remote for a project, so no default is guessed).

## 5. Using a remote instance

Once deployed, boot and use it exactly like a local project — but by running commands
directly on the VPS (SSH in, or via the second MCP server), not via a `--remote` flag on
your local `sb`/MCP tools:

```bash
ssh ubuntu@203.0.113.10 "cd \$SANDBOX_HOME/deploy-src/<project-slug> && ./sb ensure --project-dir ."
```

Then use `wp_cli`, `fs_read`, `visit`, `run_tests`, etc. through the `sandbox-myvps` MCP
connection exactly as you would through `sandbox` locally.

## 6. Explicitly out of scope (Phase 1)

- **No continuous sync daemon.** Deploy is deliberate and on-demand.
- **No VPS power management.** The VPS is persistent and user-managed — sandbox never
  starts/stops/hibernates it.
- **No multi-tenant / shared VPS.** One developer per remote target. Per-user isolation
  is a separate, much larger future effort (see `docs/remote-hosting-prd.md` §6).
- **No Herd.** Sandbox's macOS-native, Docker-less runtime has no remote equivalent —
  targeting a remote with a Herd-configured project fails cleanly (spec FR-013).

## 7. Security

- The remote MCP server binds ONLY to the VPS's Tailscale interface, never `0.0.0.0` —
  it is unreachable from the public internet by construction, reachable only from
  devices on the same tailnet.
- A per-remote bearer token (minted at `provision` time) is required on every request as
  defense in depth on top of the network boundary — enforced via a small Starlette
  middleware wrapping FastMCP's `streamable_http_app()` (FastMCP's own OAuth-oriented
  `auth=`/`token_verifier=` mechanism needs an issuer/resource-server setup that's real
  overkill for a single pre-shared secret between one client and one server).
- Secrets (SSH connection strings live in plaintext since they're not secret; the bearer
  token IS secret) are stored in `sandbox.local.yml`'s `remotes:` block — gitignored,
  `chmod 0600`, never echoed — the same store already used for pro-license keys and the
  snapshot-bridge token.

## 8. CLI + MCP surface

See `specs/014-remote-vps-hosting/contracts/cli-and-mcp.md` for the full flag/JSON-shape
reference. Summary:

| Command | Purpose |
|---|---|
| `./sb remote add <name> <ssh_url>` | Register a VPS target |
| `./sb remote list` | Show configured remotes + reachability + provisioned status |
| `./sb remote provision <name>` | Fully automated install + start the remote MCP server |
| `./sb remote up` / `down <name>` | Start/stop the remote MCP server process |
| `./sb remote remove <name>` | Forget locally — never touches the VPS |
| `./sb deploy --remote <name>` | One-way, on-demand push of local state to the VPS |

MCP tool: `remote_deploy(project_dir: str, remote: str) -> dict` — thin wrapper mirroring
`run_tests`/`run_plugin_check`'s calling convention.

## 9. Known limitation / next step

**Not yet live-verified against a real VPS** (Constitution Principle IV — unit tests
alone aren't proof of done). `specs/014-remote-vps-hosting/quickstart.md` documents the
required Phase 0 spike (prove `fs_read`/`visit`/`wp_cli` genuinely work through a
Tailscale-reached, VPS-hosted MCP server) plus 5 verification scenarios, to run against
a real, disposable VPS before this feature is considered fully done. 243 unit tests pass,
covering config read/write, SSH/git command construction, the deploy replace-not-stack
mechanism, and the MCP transport-selection branch's safety gates — but the live pipeline
(a real `sb remote provision` + `sb deploy` + booted instance, reached over an actual
Tailscale connection) has not yet been exercised end-to-end.
