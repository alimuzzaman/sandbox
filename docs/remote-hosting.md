# Remote VPS hosting for sandbox instances

## 1. What this is

`./sb remote` + `./sb deploy` let you run a sandbox instance on a VPS you already own
and manage, instead of on your local machine — reached over a public HTTPS control
endpoint by default, with Tailscale available as an explicit opt-in, and with the exact
same CLI/MCP surface as a local instance. This is a first-class capability
(`specs/014-remote-vps-hosting/`), grounded in a deeper feasibility study at
`docs/remote-hosting-prd.md` (read that doc's §0 for the resolved architecture
decisions this feature encodes).

**Not this**: a continuous file-sync daemon, an on-demand-power-managed VPS, or a
multi-tenant/shared-VPS story. See §5 for what's explicitly out of scope.

## 2. The model in one sentence

**Co-location, not remote-control.** The MCP server, `sb`, `$SANDBOX_HOME`, Docker, and
all containers move onto the VPS together — you reach them through a SECOND, separately
registered MCP server (`sandbox-<remote-name>`), not by adding a `--remote` flag to your
existing local tools. By default that second MCP server is exposed as
`https://<control-host>` through Caddy, while the MCP process itself stays bound to
`127.0.0.1`. Your local `sandbox` MCP server and all your local instances are completely
unaffected.

## 3. First-time setup

```bash
./sb remote add myvps ssh://ubuntu@203.0.113.10
./sb remote provision myvps --control-host sandbox-control.example.com
```

`provision` asks whether you want Tailscale instead of public HTTPS when run
interactively. In `--json`/non-interactive mode it defaults to HTTPS; pass
`--control tailscale` to opt into Tailscale explicitly.

For HTTPS mode, `provision` SSHes in and, non-interactively, installs Docker CE +
compose plugin, Caddy, the `sb` runtime itself, the MCP server venv, and the `visit`
tools venv (Playwright + headless Chromium — needed server-side, since `visit` must
reach `localhost:<port>` and the VPS's own `.tst` proxy). It stages the current local
sandbox checkout onto the VPS rather than assuming the sandbox GitHub repo is
anonymously cloneable. It then starts the remote MCP server (streamable-HTTP, bound to
`127.0.0.1`, never `0.0.0.0`) and configures a Caddy virtual host that proxies
`https://<control-host>` to it.

For Tailscale mode, `provision` also installs Tailscale (joining the tailnet if
`TAILSCALE_AUTHKEY` is set in your shell environment before running provision —
otherwise it installs the package and you `tailscale up` on the VPS manually once, then
re-run provision). The remote MCP server binds to the VPS's Tailscale interface instead
of loopback.

The SSH user must be able to run `sudo` non-interactively for package installs. On a
fresh VPS, either provision as a sudo-capable user with NOPASSWD configured, or do a
one-time root bootstrap to grant that user package-install rights. HTTPS mode also needs
the `--control-host` DNS name to resolve to the VPS; Tailscale mode needs either
`TAILSCALE_AUTHKEY` or a one-time manual `tailscale up`.

Sandbox opportunistically reuses one authenticated OpenSSH connection for repeated
SSH and SCP calls to the same endpoint. The control socket lives under the local
Sandbox runtime with owner-only permissions, uses OpenSSH's endpoint hash rather
than a readable host/user name, and expires after 600 idle seconds. Commands remain
independent: each keeps its own timeout, exit status, output handling, and
confirmation gate. If local multiplexing state cannot be prepared, Sandbox makes
one ordinary SSH connection; it never replays a command after launch.
The shared process runner also rejects shell-like string commands, NUL-bearing arguments or
environment values, and invalid timeout bounds before a subprocess is launched.
Runtime health probes accept only HTTP(S) URLs and finite non-negative timeouts; unsupported
schemes and malformed probe inputs fail closed.

Runtime uploads and dirty-file deployment use one streamed archive/session where
possible. This avoids one SSH channel for every `mkdir` and `scp`; the control
master still provides safe opportunistic fallback when a socket disappears or
the host is unavailable. Long-lived MCP HTTP access uses the remote server's
HTTPS/Tailscale transport rather than an SSH tunnel; a tunnel would add
forwarding lifecycle without reducing ordinary command-shell latency.

Register the second MCP server through your client’s supported secret mechanism. The
remote bearer credential is never printed, returned in JSON, embedded in a command,
or copied into a service definition. It stays in Sandbox's owner-only local secret
store and is transferred to the remote only on standard input while the remote service
credential file is created. Your local `sandbox` MCP server registration is completely
untouched.

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

Before the remote instance is considered ready, `ensure` reconciles each instance's
published WordPress, database, and Mailpit ports against listeners already present on
the host. If a stale container or unrelated process owns a recorded port, Sandbox moves
the instance to a free trio and regenerates Compose before booting. This prevents a
partial `compose up` from leaving WordPress reachable while its WP-CLI database path is
broken.

There is no project-level config for this — you always pass `--remote <name>`
explicitly (unlike `plugin-check`'s slug, there's no single obviously-correct default
remote for a project, so no default is guessed).

### One-shot deploy + public instance

For agent workflows, use the one-shot form. It deploys the current working tree, boots or
refreshes the remote WordPress instance, activates the deployed plugin slug, configures a
public HTTPS hostname through Caddy, updates WordPress `home`/`siteurl`, and returns the
URL in the JSON result:

```bash
./sb deploy \
  --project-dir /path/to/plugin \
  --remote myvps \
  --ensure \
  --expose \
  --domain default-templately-ai-builder.sandbox.asb.bd \
  --plugin-slug templately-ai-builder \
  --json
```

If `--domain` is omitted, sandbox uses `default-<project-slug>.sandbox.asb.bd`.
DNS for that hostname must already point at the VPS. In MCP, `remote_deploy(...)`
defaults to `ensure=true` and `expose=true`, so agents get the full remote instance and
public URL path unless they explicitly opt out.

### Generic Compose projects

The same command works for an explicit non-WordPress Compose project. Its
`sandbox.config.json` must declare `kind: "compose"`, the public Compose service,
the service's internal port, and a health path. `deploy --ensure` transfers the
working tree and runs the normal generic lifecycle on the remote; `--expose` routes
the resulting generic HTTP port through Caddy and returns the public URL. No plugin
activation or WordPress URL update is performed. `--plugin-slug` is WordPress-only and
is rejected for generic projects so an accidental flag cannot be silently ignored.

## 5. Using a remote instance

Once deployed, boot and use it exactly like a local project — but by running commands
directly on the VPS (SSH in, or via the second MCP server), not via a `--remote` flag on
your local `sb`/MCP tools:

```bash
ssh ubuntu@203.0.113.10 "cd \$SANDBOX_HOME/deploy-src/<project-slug> && ./sb ensure --project-dir ."
```

## Managed Compose hosts

Any Docker Compose project can carry a project-local `sandbox.hosting.yml`. It describes
a Compose web service, deployment policy,
primary hostname, aliases, redirects, and the required Cloudflare policy.

```bash
./sb host validate --project-dir /path/to/site
./sb host plan --project-dir /path/to/site --environment production --remote myvps
./sb host apply --project-dir /path/to/site --environment production --remote myvps --confirm
```

`validate` is offline. `plan` is read-only and lists only the declared hostnames;
it never prunes unrelated DNS records. `apply` is confirmation-gated: it transfers the
approved checkout, runs Compose/init health checks, validates Caddy, and then updates
only declared DNS records. Configure Cloudflare with `./sb connect cloudflare`, which
stores the token in `~/.zshrc.secrets` (owner-only and outside Git), and record the VPS
public address with `./sb remote set-origin myvps --ipv4 <address>`.

Permanent projects may declare public values plus required/generated secret mappings in
`sandbox.hosting.yml`. `./sb host secrets --project-dir /path --environment production`
reports names only; `--generate` creates declared generated values and `--set KEY`
stores a required value through a hidden prompt.

### One-time hosted WordPress login URLs

A WordPress hosting environment can opt in to a short-lived admin login link by
declaring its target MU-plugin path and login user in `sandbox.hosting.yml`:

```yaml
autologin:
  user: admin
  container_path: /var/www/html/wp-content/mu-plugins/99-sandbox-host-autologin.php
  ttl_seconds: 900
```

After a successful deployment, issue a link explicitly:

```bash
./sb host login-url --project-dir /path/to/wordpress --environment production \
  --remote myvps --confirm
```

The returned `?sandbox_autologin=` URL expires after the requested lifetime (or the
manifest default) and can set an admin session once. Sandbox stores only a SHA-256
hash in the running container, never records the token in Git or host state, and a
new link replaces the previous unused link. Treat the returned URL like a password.

The selected policy is Cloudflare-proxied DNS with Origin CA certificates and Full
(strict) TLS. Origin keys are generated on the VPS and never returned by Sandbox.

Then use `wp_cli`, `fs_read`, `visit`, `run_tests`, etc. through the `sandbox-myvps` MCP
connection exactly as you would through `sandbox` locally.

On a shared VPS that will also host other apps (for example, Next.js), route every public
app by hostname through Caddy. Sandbox's control endpoint should get its own hostname
(`sandbox-control.example.com`), and your Next.js app should get another
(`app.example.com`). Plain sandbox WordPress instances still use high ports; avoid
`domains setup` unless you intend sandbox to add public hostname routes for those sites
too.

### Disposable remote previews

`./sb preview` creates a separate, public WordPress Sandbox instance for a
temporary branch check. It uses a generated `preview-…` instance label, isolated
Docker containers/volume, a generated subdomain below `sandbox.asb.bd`, and an
expiry record stored only under `$SANDBOX_HOME/runtime/remote-previews.json`.

```bash
./sb preview create --remote myvps --project-dir /path/to/plugin --name fix-login \
  --ttl-hours 24 --confirm
./sb preview list
./sb preview destroy --remote myvps --id fix-login-<id> --confirm
./sb preview cleanup --remote myvps --confirm
```

Creation is confirmation-gated because it deploys the selected checkout, creates
one Cloudflare-proxied A record, configures a Caddy route, and starts a remote
container. `destroy` removes only the recorded Caddy fragment, named Sandbox
instance, and exact DNS record. `cleanup` removes previews whose recorded expiry
has passed; it is intentionally an explicit command, not a background reaper.

## 6. Explicitly out of scope (Phase 1)

- **No continuous sync daemon.** Deploy is deliberate and on-demand.
- **No VPS power management.** The VPS is persistent and user-managed — sandbox never
  starts/stops/hibernates it.
- **No multi-tenant / shared VPS.** One developer per remote target. Per-user isolation
  is a separate, much larger future effort (see `docs/remote-hosting-prd.md` §6).
- **No Herd.** Sandbox's macOS-native, Docker-less runtime has no remote equivalent —
  targeting a remote with a Herd-configured project fails cleanly (spec FR-014).

## 7. Security

- In HTTPS mode, the remote MCP server binds only to `127.0.0.1`; Caddy exposes it as a
  TLS virtual host. In Tailscale mode, it binds only to the VPS's Tailscale address. It
  never binds to `0.0.0.0`.
- A per-remote bearer token (minted at `provision` time) is required on every request —
  enforced via a small Starlette
  middleware wrapping FastMCP's `streamable_http_app()` (FastMCP's own OAuth-oriented
  `auth=`/`token_verifier=` mechanism needs an issuer/resource-server setup that's real
  overkill for a single pre-shared secret between one client and one server).
- SSH connection strings are stored in `sandbox.local.yml`'s `remotes:` block — gitignored
  and `chmod 0600`. They are write-only: `remote add` accepts one, but CLI and MCP output
  never reveal it. The bearer token is also stored there; it is never shown, returned,
  stored in service metadata, or passed on an argument list.
- Remote streamable-HTTP is owned by `sandbox-mcp-remote.service`, a systemd user
  service with an owner-only environment file. It is restartable and reboot-recoverable
  only when user lingering is enabled. Sandbox refuses wildcard/public listener binds.
- A remote stop controls only the proven Sandbox service unit. Sandbox never scans or
  terminates generic streamable-HTTP processes by their command-line flags.

## 8. CLI + MCP surface

See `specs/014-remote-vps-hosting/contracts/cli-and-mcp.md` for the full flag/JSON-shape
reference. Summary:

| Command | Purpose |
|---|---|
| `./sb remote add <name> <ssh_url>` | Register a VPS target |
| `./sb remote list` | Show configured remotes + reachability + provisioned status |
| `./sb remote provision <name> --control-host <host>` | Fully automated install + start the remote MCP server over public HTTPS |
| `./sb remote provision <name> --control tailscale` | Same, but use Tailscale instead of public HTTPS |
| `./sb remote service status <name> --json` | Read-only owned-service, listener, and recovery evidence |
| `./sb remote service migrate <name> --plan --json` | Read-only systemd service migration plan |
| `./sb remote service migrate <name> --confirm --json` | Install the protected owned service after explicit confirmation |
| `./sb remote service stop <name> --confirm --json` | Stop only the selected proven service unit |
| `./sb remote up` / `down <name> --confirm` | Legacy-compatible lifecycle entrypoints; planning is the default and migrated remotes use the owned service |
| `./sb remote remove <name>` | Forget locally — never touches the VPS |
| `./sb deploy --remote <name>` | One-way, on-demand push of local state to the VPS |
| `./sb deploy --remote <name> --ensure --expose [--domain <host>]` | One-shot deploy, boot/refresh the remote WP instance, activate the plugin, and expose a public HTTPS URL |

MCP tool:
`remote_deploy(project_dir: str, remote: str, ensure: bool = True, expose: bool = True, domain: str | None = None, plugin_slug: str | None = None) -> dict`.
It mirrors `run_tests`/`run_plugin_check`'s calling convention and returns `instance`
plus `url` when exposure succeeds.

`remote service status` checks the selected unit's non-secret ownership marker and
runtime revision, expected bind/port, systemd activity/enablement, user linger, local
listener scope, and an authenticated `/mcp` probe. It treats unavailable evidence as
degraded; it never reads a credential into command arguments or output.

## 9. Known limitation / next step

**Live-verified against a fresh Ubuntu 24.04 VPS.** A real run against
`alim@212.47.72.49` installed Docker CE + compose, Caddy, the staged sandbox runtime, the
MCP venv, and the Playwright/Chromium tools venv. The remote now reports provisioned at
`https://sandbox-control.asb.bd`; Caddy owns public `80/443` by hostname, while the MCP
service itself binds only to `127.0.0.1:9174`. Bearer-auth probing reaches the app (auth
failures return `401`; the verified token now reaches MCP-level responses instead).
The HTTPS endpoint was then registered at its `/mcp` streamable-HTTP route and
successfully ran `fs_read`, `visit`, `wp_cli`, and `run_tests` against the VPS-side
`html-social-share-buttons` project. `specs/014-remote-vps-hosting/quickstart.md`
is satisfied for the public-HTTPS path.
