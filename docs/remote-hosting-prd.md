# Remote runtime hosting — feasibility study & PRD

Author: drafted 2026-07-09 (design-fidelity-diff session). **Status: ARCHITECTURE
DECIDED — ready for `speckit-specify`.** Section 0 below records a follow-up
conversation that resolved every load-bearing open question from §7 (transport, VPS
lifecycle, provisioning, and — critically — a simplified source-of-truth mechanism
that replaces the mutagen-sync idea this doc originally floated). The rest of this
document (§1–§8) is kept as-written: it's still the grounded technical research this
decision rests on, but §7's open questions are now historical — see §0 for the actual
answers before reading them as if still open. This is still not itself the spec (no
FRs, no acceptance gate, no task list) — the next step is `speckit-specify`, not a PR.

Companion reading: `docs/multi-instance-spec.md` (the labelled-instance primitive) and
`docs/ci-e2e-runner-spec.md` (fan-out + async jobs). This doc reuses their vocabulary
(instance, label, root, fan-out) and assumes you've read them.

---

## 0. Resolved follow-up decisions (post-research)

A follow-up conversation, after this doc's initial research, resolved the questions in
§7 and replaced §3/§6's "mutagen sync" idea with something simpler:

- **Source of truth: local, deployed one-way and on-demand — no continuous sync.**
  Rejects the mutagen/rsync bidirectional idea in §3/§6 entirely (its real risks —
  conflict resolution, races between an edit and a stale sync, a daemon to keep
  alive — are avoided by not having a sync daemon at all). Instead: `sb remote
  provision <name>` sets up a plain (non-bare) git repo on the VPS with
  `receive.denyCurrentBranch=updateInstead` (pushing directly updates its checked-out
  working tree — no bare-repo dance). `sb deploy` then (1) `git push`es the current
  local `HEAD` to it — works even for unpushed WIP branches, and only new git objects
  transfer each time; (2) captures the local working tree's uncommitted changes
  (tracked-file diff **plus** untracked files, since plain `git diff` misses those)
  and applies them on top. Each deploy **replaces** the uncommitted layer rather than
  stacking (reset the VPS tree to the just-pushed clean `HEAD` first, then apply the
  *current* diff fresh) — a stale diff from an earlier deploy can never silently
  survive underneath a new one. "Is my code live on the VPS" always has exactly one
  answer: "as of my last `sb deploy`."
- **Transport: Tailscale/WireGuard mesh**, not an SSH reverse tunnel (more robust
  across network changes/sleep/multiple devices). The MCP server binds only to the
  VPS's Tailscale interface, never `0.0.0.0` (§6's non-negotiable holds), plus a
  per-remote bearer token as defense in depth.
- **VPS lifecycle: persistent, user-managed.** The user provides SSH access to an
  already-running server they manage themselves. Sandbox never powers the machine
  on/off — no cloud-provider API integration, no on-demand start/stop. Answers §7 Q5
  (persistent, not on-demand) and implicitly Q2 (one VPS per developer, user-owned).
- **Provisioning: fully automated over SSH**, mirroring the existing
  `scripts/install-macos.sh` / `scripts/install-ubuntu.sh` bootstrap pattern but run
  remotely. `sb remote provision <name>` SSHes in and runs a new
  `scripts/install-remote.sh`-style script: installs Tailscale (joins the tailnet),
  Docker CE + compose plugin, the `sb` runtime, and the `visit` tools venv
  (Playwright/headless Chromium) — one command, no manual copy-paste.

§7's remaining open items (screenshot/artifact return format; whether this is the same
need as the README's "remote API surface" idea) are still genuinely open — nothing
above resolves those. Q1 (source of truth) and Q4 (editing surface) are answered by the
deploy mechanism above: Claude Code's local `Read`/`Write`/`Edit` stays the *only*
editing surface; the VPS is a deploy target, never edited directly. Q3 (transport) is
answered above (Tailscale, not tunnel).

---

## 1. Summary / recommendation

**Feasible? Yes — but not the way it first looks.** The obvious approach ("just point
`docker` at a remote daemon with `DOCKER_HOST=ssh://…`") is a two-line change that
silently breaks roughly half the tool surface, because this codebase is built on
*local-filesystem co-location*: the docker daemon, the plugin source, `$SANDBOX_HOME`
state, the `visit` screenshot runner, and the snapshot tarballs are all assumed to live
on the same machine and share one filesystem namespace. A remote daemon severs that
assumption everywhere at once — bind mounts point at paths that don't exist on the
remote, `fs_read`/`fs_write`/`tail_log` read empty local directories, `visit` hits a
`localhost:<port>` that's now on another host, and `./sb snapshot` `tar`s a directory
the daemon can't see.

**Recommended approach: move the MCP server *and* the runtime onto the VPS together
(co-locate), and reach it from a local thin client over an SSH-tunneled (or
Tailscale-meshed) HTTP transport.** Counter-intuitively this is the option that requires
the *least* change to the tool code: because `fs_read`, `visit`, `db_query`, snapshots,
and the compose bind mounts all run *on the VPS where the paths and the daemon are
co-located*, they keep working essentially unmodified. This is the same architecture VS
Code chose for "Dev Containers over Remote-SSH" (run the agent on the remote host, not
the daemon-over-SSH variant) and the same shape as Coder/Codespaces (a per-workspace
agent process on the box, a thin local client). The genuinely hard problem it *relocates*
(rather than solves for free) is **the plugin source**: today the whole value
proposition is "edit your local git worktree, it's live in the container in seconds via a
bind mount." On a remote daemon the worktree must exist *on the VPS* to be bind-mounted,
so we need either a fast bidirectional file-sync daemon (mutagen/rsync) or an explicit
"the repo lives on the VPS and you edit it there" model. That decision — where the source
of truth for code lives — is the single biggest open question and needs a maintainer
call before this can become a spec.

My recommendation is to pursue it in the narrow form first (Phase 1: one VPS, one
developer, automated provisioning, source deployed on-demand — see §0) and treat the
multi-tenant / team-shared VPS as a separate, later, much larger effort — the security
and isolation story for a shared internet-reachable box is a project in itself.

---

## 2. Current architecture recap (grounded)

Everything below is verified against the code on the `design-fidelity-diff` branch.

**One machine, one filesystem, three processes.** Today the Docker daemon, all
WordPress/MySQL/Mailpit containers, the `sb` Python CLI, and the MCP server process all
run on the user's local machine. `$SANDBOX_HOME` (default `~/sandbox`,
`sandbox_core.sandbox_base()`) holds every byte of runtime state: `registry.json`, the
generated compose files (`RUNTIME_DIR/compose/<instance>.yml`), each instance's
bind-mounted WordPress tree (`RUNTIME_DIR/wp-<instance>`, `_instances.wp_dir()`),
snapshots, seeds, the tools venv, and `sandbox.local.yml` secrets.

**How the MCP server is launched and reached — stdio subprocess.** `./sb setup`
registers a *single* user-scope MCP server named `sandbox`
(`sandbox/core/_integ.py:register_claude_user_scope`). The registration entry
(`_build_mcp_entry`) is literally `{"command": "sb", "args": ["mcp"]}` — Claude Code
spawns `sb mcp` as a child process and talks to it over **stdio**. `sb mcp` execs the
venv's `mcp/wp-server/server.py`, which calls `mcp.run()` — and FastMCP's default
transport is stdio. There is no network transport configured anywhere today. The server
imports `sandbox_core` from the repo checkout and resolves `$SANDBOX_HOME` identically to
the CLI, so the two processes agree on where state lives.

**How a tool call finds its target — the registry, by local path.** Every tool takes
`project_dir`. `mcp/wp-server/app.py:_project_instance()` calls
`sandbox_core.find_project_root(project_dir)` (walks up to the nearest
`sandbox.config.*`/`.git`) and then `registry_list_for_root(root)` to get the instance
*name*. The registry (`sandbox_core.py`, v2 schema) is keyed
`"<canonical-root>::<label>"` and stores ports, server tier, status, and per-instance
secrets. `find_project_root` **rejects any path outside `$HOME`** (or
`SANDBOX_PROJECT_ROOTS`) — a local-filesystem assumption baked into path safety.

**How docker is actually invoked — the one clean seam.** All container operations funnel
through `_docker.compose(*args, instance=…)`, which builds
`["docker", "compose", "-p", project_name, "-f", <compose file>, "--project-directory",
ROOT, *args]` and hands it to `_ui.run()`, which is just
`subprocess.run(cmd, cwd=str(ROOT), **kw)` — **inheriting the process environment**. This
is the seam that *would* honor a `DOCKER_HOST` env var. It's also the only clean seam;
everything downstream of it assumes local paths.

**What assumes a shared local filesystem (the wide part).**

- **Bind mounts.** `ensure_instance` → `_build_instance_block` mounts the plugin source at
  the *same absolute host path inside the container*
  (`${SANDBOX_PLUGINS_HOST}:${SANDBOX_PLUGINS_HOST}`, CLAUDE.md gotcha #3) and mounts
  `wp_dir(instance)` for WordPress core. Both are host paths that must exist *on the
  daemon's machine*.
- **File tools read the disk directly.** `mcp/wp-server/tools/fs.py`
  (`fs_read`/`fs_write`/`fs_list`/`tail_log`) do `Path(...).read_bytes()` /
  `write_text()` against `_wp_root(instance) = RUNTIME_DIR/wp-<instance>` — no container,
  no daemon, just local FS. Same for `db_query`/snapshots reading exported files.
- **`visit` runs a local browser against `localhost:<port>`.**
  `mcp/wp-server/tools/net.py:visit` shells a local Playwright/Chromium
  (`TOOLS_VENV_PY` + `tools/visit/visit.py`) at whatever URL you pass; the instance's URL
  is `http://localhost:<port>` (`_site_url`) or a host-side `https://<name>.tst` proxy.
  `pixelmatch_diff` reads two local PNG paths.
- **Snapshots assume local disk + a shared mount.** `sandbox/commands/data.py:cmd_snapshot`
  runs `compose("run","--rm","-v", f"{snap_root}:/snapshots", "wpcli", "db","export",…)`
  — the `-v {snap_root}:…` bind mount only works if `snap_root` (a `$SANDBOX_HOME` path)
  exists on the daemon host — then `tar`s `wp_dir(inst)/wp-content/uploads` with a **local**
  `tar` process.
- **The clean-URL proxy + `secure-at-create`** (`_instances.py:_secure_at_create`, the
  Caddy proxy under `RUNTIME_DIR/proxy`, the `lo0` alias LaunchDaemon in `_integ.py`) are
  all host-local networking tricks tied to the developer's own loopback and DNS.
- **The Herd runtime (`sandbox/core/_herd.py`)** is a *host-native, macOS-only, no-Docker*
  path (Laravel Herd + host MySQL). It is fundamentally local and out of scope for remote
  hosting — a remote-Linux target implies the Docker path only.

**Fan-out and async jobs** (`_fanout.run_across_instances`,
`_asyncjobs.launch_background_job` under `$SANDBOX_HOME/runtime/async-jobs/`) already
assume Docker-on-the-same-host, and the CI runner's `host.docker.internal:<port>` trick
(`docs/ci-e2e-runner-spec.md` §3.6) means "the machine the daemon runs on" — which is
exactly the semantic that shifts under our feet when the daemon is remote.

**Takeaway:** there is *one* narrow seam (`_ui.run` env → `DOCKER_HOST`) and a *wide*
surface (every filesystem/localhost assumption). Any remote design lives or dies on how
it handles the wide surface, not the narrow seam.

---

## 3. Candidate architectures (MCP placement × bridging)

Three models, framed by the two coupled questions from the brief: *where does the MCP
server run* and *how does the local Claude Code reach the runtime*.

### Model A — Local MCP, remote daemon (`DOCKER_HOST=ssh://…` / docker context)

Keep the MCP server local (stdio, unchanged). Set `DOCKER_HOST=ssh://user@vps` (or a
`docker context`) so `_ui.run`'s `docker compose` calls execute against the remote
daemon. This is the "two-line change."

It works for the *container lifecycle* and breaks everything filesystem-shaped:

- Bind mounts silently mis-resolve. Per Docker's own documented behavior, compose bind
  mounts with a remote host resolve paths *relative to the client*, and the daemon looks
  for that path *on the server* — where it doesn't exist, so the container gets an **empty
  directory** (docker/compose #8484, #11867). The plugin source and `wp_dir` mounts
  produce a blank WordPress. The entire "edit-local-live-in-container" value prop is gone.
- `fs_read`/`fs_write`/`tail_log`/`db_query` read `RUNTIME_DIR/wp-<instance>` on the
  *local* disk — now empty (the real files are in the remote daemon's storage). They
  return "not found" or stale data.
- `visit` hits `localhost:<port>` locally — nothing is published there; the ports are on
  the VPS. Every screenshot fails.
- `./sb snapshot`'s `-v {snap_root}:/snapshots` mount and `tar` of local uploads both
  break for the same path-mismatch reason.

**Verdict: reject as a primary architecture.** It is the trap the brief warns about —
deceptively small, semantically catastrophic. It's only viable if paired with a full
file-sync layer that makes local paths exist identically on the remote, at which point
you've done most of Model B's work anyway but kept all the fragility.

### Model B — Remote MCP server co-located with the runtime, thin local client (RECOMMENDED)

Move the MCP server, the `sb` CLI, `$SANDBOX_HOME`, the Docker daemon, and all containers
onto the VPS. The server switches from stdio to **streamable-HTTP** transport
(FastMCP/`mcp` supports it — `mcp.run(transport="streamable-http")`). Local Claude Code
connects to it over the network, reached through an **SSH reverse tunnel** or a
**Tailscale/WireGuard mesh** so nothing is exposed to the public internet.

The pivotal property: **every filesystem/localhost tool now runs on the box where the
paths and daemon are co-located, so `fs_read`, `visit`, `db_query`, snapshots, and the
bind mounts keep working with little-to-no code change.** `visit` hits
`localhost:<port>` and it's *right there*; `_wp_root` is a real local path *on the server*;
`snapshot`'s `-v` mount resolves. The blast radius collapses from "every tool" to "the
transport + how the client reaches it + where the source lives."

Trade-offs specific to this codebase:

- **The plugin source problem (the crux).** Claude Code's own `Read`/`Write`/`Edit` and
  the user's editor operate on the *local* filesystem, but the bind mount needs the source
  *on the VPS*. Options: (B1) a fast bidirectional sync daemon (mutagen or `rsync`/SSHFS)
  mirrors the local worktree → a VPS path that gets bind-mounted; (B2) the repo lives on
  the VPS and the user edits over Remote-SSH / a synced mount. B1 preserves today's UX best
  and is what remote-docker dev setups converge on. **RESOLVED (§0): neither — a THIRD
  option, one-way on-demand `sb deploy` (git push + diff-apply), was chosen instead. It
  keeps B1's "edit locally" UX without a continuous-sync daemon's failure modes.**
- **`find_project_root` path-allowlisting** rejects non-`$HOME` paths; on the server the
  synced worktree lives under the VPS user's home, so this still holds — but the
  `project_dir` the *client* sends is a *local* path that must be translated to the *remote*
  path. Needs a client↔server path-mapping step.
- **Screenshots/artifacts come back over the wire.** `visit --screenshot` and
  `pixelmatch_diff` write PNGs on the *server*; the client (and the user) need them locally.
  A small artifact-fetch step (the transport already exists) covers this — latency is one
  file transfer, not a round-trip per pixel.
- **Latency lands where it hurts least.** wp-cli, REST, SQL, phpunit, and container boots
  all run server-side at server-local latency (fast). Only the *control channel*
  (tool-call request/response + artifact transfer) crosses the network. This is the right
  place to pay latency — the opposite of Model A, where every `docker exec` round-trips.
- **Secrets stay on the server.** `sandbox.local.yml`, `ci_secrets`, app passwords, bridge
  tokens (`_build_instance_block`) live in `$SANDBOX_HOME` on the VPS and are never sent to
  the client. This actually *improves* the secrets posture (CLAUDE.md's never-echo rule) —
  they never leave the box.

### Model C — Backend abstraction + thin remote agent (authenticated HTTPS API), MCP stays local

Introduce a `RuntimeBackend` abstraction. The MCP server stays local (stdio), but instance
operations dispatch to a pluggable backend: `local-docker` (today) or `remote-agent`. The
remote agent is a small authenticated HTTPS daemon on the VPS that owns docker and exposes
*narrow, safe* operations (boot instance, exec wp-cli, read file, take screenshot,
snapshot) — **the raw Docker socket is never exposed.** Path-returning functions and the
fs/visit/snapshot tools become backend-aware: for a remote instance they proxy through the
agent's file/exec API instead of touching a local `Path`.

- **Pros:** keeps the local-first UX (client-side editing "just works" if the agent also
  handles source sync); never exposes docker; the cleanest security boundary (the agent
  defines exactly what's permitted); this is essentially "build a mini-Coder." Orthogonal to
  transport — the MCP client experience is unchanged.
- **Cons:** the most engineering by far. Every filesystem-assuming tool (`fs.py`,
  `data.py`, `net.py`, snapshots) needs a remote code path; you're re-implementing SFTP +
  remote-exec + screenshot-fetch behind a bespoke API and maintaining two backends forever.
  It's the principled *destination* but a poor *starting point*.

### Comparison

| Dimension | A: Local MCP + remote daemon | B: Remote MCP, co-located (REC) | C: Backend abstraction + agent |
|---|---|---|---|
| MCP server runs | Local (stdio, unchanged) | **On VPS** (streamable-HTTP) | Local (stdio, unchanged) |
| Transport to reach runtime | stdio (local) + `DOCKER_HOST` ssh | HTTP over SSH tunnel / Tailscale | stdio + agent HTTPS API |
| Bind mounts (plugin src, wp_dir) | **Broken** (path mismatch) | Work (co-located on VPS) | Work (agent-mediated / synced) |
| `fs_read`/`fs_write`/`tail_log` | **Broken** (read empty local FS) | Work unchanged (server-local) | Rewritten to proxy via agent |
| `visit` / screenshots | **Broken** (localhost is remote) | Work; artifact fetched back | Rewritten; run via agent |
| Snapshots (`-v` mount + `tar`) | **Broken** | Work unchanged | Rewritten via agent |
| wp-cli latency | High (every exec round-trips) | Low (server-local) | Low (server-local) |
| Code change size | Tiny seam, huge breakage | Transport + source-sync + path-map | **Largest** (two backends) |
| Docker socket exposure risk | High (ssh mitigates) | None (tunnel, no daemon port) | None (agent only) |
| Secrets location | Split (local `.local.yml`) | **All on VPS** (best) | On VPS |
| Multi-user / team | Poor | Per-dev VPS: good; shared: hard | Best (agent enforces isolation) |

**Recommendation: Model B for Phase 1**, because it makes the *tool surface* work with the
least code change by co-locating everything, and pays latency on the cheap channel. Adopt
**Model C's `RuntimeBackend` seam as the long-term direction** — but only after B proves
the demand. Model A is a documented anti-pattern here.

> Note: B and C are not mutually exclusive. The `RuntimeBackend` abstraction (C) is the
> right internal seam *regardless*; Model B is "the first backend is 'the whole server runs
> remote,' reached over a tunnel." The pragmatic path is to build the seam thin, ship B, and
> let C's agent grow into it if a shared-VPS multi-tenant need ever materializes.

---

## 4. Code / architecture split proposal (naming real seams)

This section is what *would* change, grounded in real functions — not yet a task list.

### 4.1 A `RuntimeBackend` seam around `_docker.compose` / `_ui.run`

The single narrowest seam is `_ui.run()` and `_docker.compose()`. Introduce a
`RuntimeBackend` protocol with two implementations:

- `LocalDockerBackend` — today's behavior verbatim (subprocess `docker compose`, local
  paths).
- `RemoteBackend` — used by Model B to mean "this instance is owned by a remote sandbox
  server"; in the co-located design most of its methods are *no-ops on the client side*
  because the server itself runs `LocalDockerBackend` on the VPS. The client's job shrinks
  to transport + source-sync + path-mapping.

Minimum surface the backend must own: `compose(*args)`, `wp_root(instance) -> handle`,
`read_file` / `write_file` / `list_dir`, `screenshot(url) -> bytes`, `snapshot` /
`restore`. In Model B these all resolve *server-side*, so the change is mostly "thread a
backend handle through," not "rewrite each tool."

### 4.2 Registry becomes runtime-aware

`sandbox_core.registry_put` / `registry_get` / `registry_list_for_root` currently store
ports/status/secrets keyed by `root::label`. Add a `runtime` field to each entry, e.g.
`"runtime": {"kind": "local"}` or `"runtime": {"kind": "remote", "host": "myvps"}`. This is
a **v2→v3 registry migration** in the exact shape the multi-instance doc already
established (`_migrate_registry_v1_to_v2`, `sandbox_core.py`): default every existing entry
to `{"kind": "local"}`, idempotent, guarded by `_registry_lock`. `_project_instance` /
`_cwd_instance` then also resolve *which backend* an instance lives on.

Crucially: **`runtime` is a new axis orthogonal to `label`.** `label` = which stack within
a root (the multi-instance primitive); `runtime` = which machine hosts it. A root could in
principle have `default` local and `qa` remote — though Phase 1 should constrain a project's
instances to one backend to keep the mental model simple.

### 4.3 Path-returning functions must stop leaking bare local `Path`s

`_instances.wp_dir()`, `plugins_dir()`, `snapshots_dir()`, `_wp_root()`, `COMPOSE_DIR`, and
`compose_file()` all return local `Path` objects that callers treat as directly readable.
Under Model B they stay valid *on the server*, but any code path that runs *on the client*
(the CLI's own file reads, artifact handling) must go through the backend. In Model C they
must *all* become backend-aware handles. This is the widest change and the reason Model B
(where the server keeps using local `Path`s natively) is cheaper.

### 4.4 The filesystem tools (`mcp/wp-server/tools/fs.py`, `data.py`, `net.py`)

- Under **Model B**: essentially unchanged — they run on the server, `_wp_root(inst)` is a
  real server path. The only addition is an **artifact-return** convention: `visit
  --screenshot` and `pixelmatch_diff` write server-side PNGs; the tool result must include a
  way for the client to fetch them (inline base64 for small images, or a
  fetch-by-id endpoint over the transport).
- Under **Model C**: `fs_read`/`fs_write`/`fs_list`/`tail_log` and the snapshot
  export/`tar` in `data.py:cmd_snapshot` need explicit `remote-agent` code paths.

### 4.5 `visit` placement

Decision: in Model B, `visit` runs **on the server** (it must, to reach `localhost:<port>`
and the `.tst` proxy). That means the VPS needs the Playwright/Chromium tools venv
(`TOOLS_VENV_PY`) provisioned — a headless-Chromium-on-a-Linux-VPS story, which is standard
but adds an apt/deps burden to remote provisioning. The screenshot bytes travel back over
the transport. Running `visit` *locally against a tunneled public URL* is possible but
worse: it can't auto-login through the host proxy cleanly and it re-introduces a
localhost-vs-remote URL mismatch of exactly the kind the CI runner already got burned by
(`docs/ci-e2e-runner-spec.md` §3.6, the `.tst`-URL-unreachable-in-container bug).

### 4.6 The `host.docker.internal` semantics shift (CI/e2e)

The CI runner passes `WP_BASE_URL=http://host.docker.internal:<port>` and
`--add-host=host.docker.internal:host-gateway` so an `act` job container can reach the
instance (`docs/ci-e2e-runner-spec.md` §3.6). On a remote daemon "host" means *the VPS*,
which is actually still correct *as long as `act` also runs on the VPS* — and it must,
since `act` needs the same Docker daemon. So the CI/e2e fan-out (`_fanout.py`,
`_asyncjobs.py` under `$SANDBOX_HOME/runtime/async-jobs/`) should run **entirely
server-side** in Model B, launched via the remote CLI. This is consistent and clean — but
it means `./sb ci run --remote myvps` dispatches the whole run to the VPS, and
`./sb async-job` polling must read the job dir *on the VPS* (another reason the server-side
co-location model is coherent and the daemon-over-SSH model is not).

---

## 5. CLI / UX proposal

Design goal: a remote target is an **opt-in, per-project-or-per-invocation** axis that
composes cleanly with the existing `--label` mechanism and leaves every existing local
command byte-identical when no remote is named. This mirrors how `--label` was added as a
purely additive optional axis (`docs/multi-instance-spec.md` §6).

### 5.1 New subcommand group: `./sb remote`

```
./sb remote add <name> ssh://user@host[:port]   # register a VPS target (stored in sandbox.local.yml)
./sb remote list                                # show configured remotes + reachability
./sb remote provision <name>                    # install docker + sandbox runtime + start server on the VPS
./sb remote up <name> / down <name>             # open / close the tunnel (or bring the mesh iface up)
./sb remote remove <name>
```

Remotes live in `sandbox.local.yml` under a new `remotes:` block (per-machine, gitignored —
consistent with where secrets already land). Auth is **SSH key-based** (Docker/DOCKER_HOST
don't support password auth anyway, per the VS Code remote-docker docs) plus a
**per-remote bearer token** minted at `provision` time for the MCP HTTP transport, stored
in `sandbox.local.yml` and never echoed (CLAUDE.md secrets rule). mTLS is a stronger
option for the transport if we ever move past a single tunnel.

### 5.2 Targeting a remote — a new axis, orthogonal to `--label`

- **Per-invocation:** `./sb ensure --project-dir X --remote myvps [--label qa]`.
- **Per-project default:** a `"runtime": "myvps"` key in `sandbox.config.json` (or the
  gitignored override), so a project *always* boots remote without a flag.
- **MCP tools** gain an optional `remote: str | None = None` param alongside the existing
  `label`, threaded through the same `_project_instance` resolver — but note that once an
  instance is *registered* as living on `myvps`, the registry entry's `runtime` field
  already records that, so `wp_cli(project_dir=X)` routes to the right backend with no
  per-call `remote=` needed (the parameter is only for *minting* / disambiguation, exactly
  like `label`/`create`).

### 5.3 First-time setup transcript (Phase 1, single VPS, single dev)

**Superseded by §0's resolution** — updated below to match: Tailscale mesh (not an SSH
tunnel) and `sb deploy` (not mutagen sync).

```
$ ./sb remote add myvps ssh://ubuntu@203.0.113.10
  ✓ added remote 'myvps' (ssh://ubuntu@203.0.113.10) to sandbox.local.yml
  next: ./sb remote provision myvps

$ ./sb remote provision myvps
  ▸ checking SSH reachability … ok (key auth)
  ▸ installing + joining Tailscale on the VPS … ok (tailnet address: myvps.tailnet.ts.net)
  ▸ installing Docker CE + compose plugin on the VPS … ok
  ▸ installing the sandbox runtime ($SANDBOX_HOME on the VPS) … ok
  ▸ provisioning the visit tools venv (Playwright + headless Chromium) … ok
  ▸ setting up a deploy-target git repo (receive.denyCurrentBranch=updateInstead) … ok
  ▸ starting the sandbox MCP server (streamable-HTTP, bound to the Tailscale iface only) … ok
  ▸ minting a per-remote bearer token … stored (not shown)
  ✓ 'myvps' ready at myvps.tailnet.ts.net — reachable only over the tailnet.

$ ./sb setup --remote myvps
  ✓ registered MCP server 'sandbox-myvps' (transport: http,
    url: http://myvps.tailnet.ts.net:<mcp-port>, auth: bearer) at user scope.
```

Then, from a plugin dir, deploying on-demand instead of continuously syncing:

```
$ cd ~/Sites/git/embedpress
$ ./sb deploy --remote myvps
  ▸ pushing HEAD (a1b2c3d) → myvps:~/sandbox/src/embedpress … ok (git push, 0.4s)
  ▸ resetting VPS working tree to a1b2c3d … ok
  ▸ applying uncommitted changes (3 modified, 1 untracked) … ok
  ✓ deployed. myvps now reflects your working tree as of this command.

$ ./sb ensure --project-dir . --remote myvps
  ▸ ensure_instance on myvps … instance 'embedpress' (WP=8188 server=nginx)
  ✓ ready → https://embedpress.<vps-tld>  (reachable over the tailnet)
```

Every existing local command is unchanged: no `--remote`, no `remotes:` block → identical
to today. This is the release gate, same discipline as the multi-instance `--label` axis.

---

## 6. Non-functional risks (honest)

- **The plugin-source transfer was the hard problem — largely defused by §0's `sb
  deploy` resolution, not solved by brute force.** The original concern here was
  mutagen/rsync bidirectional sync: conflict resolution when both sides change, large
  `node_modules`/`vendor` trees, `.gitignore` divergence, and the race where Claude
  edits a file locally, the container hasn't seen the sync yet, and a test runs against
  stale code. Choosing one-way, on-demand `sb deploy` (git push + diff-apply) instead of
  a continuous daemon removes the conflict-resolution and staleness-race classes of
  failure entirely — there is no "both sides changed" because only one side (local) ever
  changes, and `.gitignore`-respecting git tooling means large build-artifact trees
  never transfer at all. What's left: symlink handling (the sandbox relies on symlinked
  plugins at depth-1, CLAUDE.md gotcha #2) needs verifying against the VPS's own
  deploy-target checkout, and — the one real remaining risk — a user forgetting to
  `sb deploy` before testing and being confused why the VPS doesn't reflect their latest
  edit. A visible "deployed as of commit X, Y uncommitted files ago" status line in
  `sb remote list`/`sb ensure` output mitigates that.
- **Never expose the raw Docker socket.** An internet-reachable Docker daemon is
  root-equivalent RCE — a well-known catastrophic mistake. The design must **never** publish
  the daemon's TCP port or the MCP HTTP port to `0.0.0.0`. Per §0's resolution, bind the
  MCP server to the VPS's **Tailscale interface only** (never `0.0.0.0`, never plain
  `127.0.0.1` alone since that wouldn't be reachable off-box at all) plus a per-remote
  bearer token as defense in depth. Validate the `Origin` header on the HTTP transport
  (MCP spec guidance against DNS-rebinding). No exceptions for "just for testing."
- **Latency on wp-cli-heavy loops.** Model B puts wp-cli server-side (good), but the
  *control channel* still adds a per-tool-call RTT. A `fix` loop that fires 30 small
  `wp_cli`/`db_query` calls will feel each RTT. Mitigation: batch where possible, keep the
  tunnel warm, prefer `wp_eval_live`/one-shot scripts over many round-trips. Honestly
  assess: for a dev on a 150ms link this is noticeable but tolerable; for a dev on a 400ms
  intercontinental link it may be worse than local Docker.
- **Cost model.** A persistent VPS big enough to run several WordPress+MySQL stacks
  (multi-instance, CI fan-out at concurrency ~4) is a real monthly cost that runs 24/7
  whether or not you're working. On-demand start/stop (boot the VPS when `./sb remote up`,
  hibernate on idle) cuts cost but adds cold-start latency and provisioning complexity.
  Phase 1 should assume a persistent box and measure real utilization before optimizing.
- **Snapshot/backup tooling assumes local disk.** `cmd_snapshot`/`cmd_restore` (and the
  `sb web` snapshot bridge, `_bridge.py`) write tarballs under `$SANDBOX_HOME`. On a remote
  they land on the *VPS* disk — which is fine functionally, but "my snapshots are on a
  server I might tear down" is a data-durability change users must understand. Snapshots
  become another thing that needs a fetch-to-local or object-store backup story.
- **Herd is simply out.** The entire `_herd.py` path (macOS-native, host MySQL, no Docker)
  has no remote-Linux analog. `--remote` must hard-error on `server: herd`.
- **Multi-tenant / shared VPS is a separate, much larger project.** One-VPS-per-developer
  (Model B Phase 1) is tractable. A *team-shared* VPS running many devs' Claude Code
  sessions needs per-user auth, per-project network/namespace isolation, resource quotas,
  and a real answer to "dev A's `wp_exec` can't touch dev B's containers or read dev B's
  `sandbox.local.yml` secrets." That is essentially rebuilding Coder's workspace-isolation
  layer and should not be smuggled into this feature's scope.
- **`find_project_root`'s `$HOME` allowlist + client↔server path translation.** The
  `project_dir` the client sends is a *local* path; the server must map it to the *synced*
  path under its own `$HOME`. Getting this mapping wrong is a whole class of confusing "no
  instance for this project" errors. Needs an explicit, tested translation layer.
- **`act` on the VPS.** The CI runner requires `act` installed (`docs/ci-e2e-runner-spec.md`
  §5). Remote provisioning must install it server-side, and the Apple-Silicon
  `--container-architecture` caveat flips (the VPS is likely x86-64, which is actually
  *simpler* — but it's a different arch than the dev's Mac, so any arch-sensitive test
  behavior shifts).

---

## 7. Open questions (need a maintainer decision before a spec)

**RESOLVED, see §0** — Q1, Q2, Q3, Q4, Q5 below were all settled in a follow-up
conversation; kept here verbatim as the original framing, each annotated with its
resolution. Q6 and Q7 are still genuinely open.

1. ~~**Where does the source of truth for code live?**~~ **RESOLVED (§0): local
   worktree, deployed one-way/on-demand via `sb deploy` (git push + diff-apply) — NOT
   mutagen sync, and NOT edit-on-the-VPS.** (a) Local worktree synced to the VPS
   (mutagen), preserving today's edit-locally UX; or (b) the repo lives on the VPS and
   the user edits over Remote-SSH. This is *the* decision — it shapes everything else.
2. ~~**One VPS per developer, or a shared team VPS?**~~ **RESOLVED (§0): per-developer,
   user-owned/managed, matching the Phase 1 recommendation below.** Strongly recommend
   per-dev for Phase 1; confirm we're not implicitly on the hook for multi-tenant
   isolation.
3. ~~**Transport: SSH reverse tunnel vs. Tailscale/WireGuard mesh?**~~ **RESOLVED (§0):
   Tailscale/WireGuard mesh.** Tunnel is zero-extra-infra but flaky on network changes;
   mesh is more robust and multi-device but adds a dependency. Which do we standardize
   on?
4. ~~**Does Claude Code's *local* `Read`/`Write`/`Edit` stay the editing surface?**~~
   **RESOLVED (§0): yes — it stays the only editing surface; `sb deploy` pushes to the
   VPS, the agent never edits there.** With sync pushing to the VPS — or do we accept a
   split where the agent edits locally and only the *runtime* is remote? (Ties to Q1.)
5. ~~**Persistent vs. on-demand VPS**~~ — **RESOLVED (§0): persistent, user-managed; no
   cold-start/hibernate logic in scope.** Is cold-start latency acceptable in exchange
   for not paying 24/7?
6. **Screenshot/artifact return** — inline base64 (simple, bloats tool results) vs. a
   fetch-by-id endpoint (cleaner, more transport surface)? **Still open** — not
   addressed in the follow-up conversation; needs a decision during `speckit-plan`.
7. **Is this actually cheaper/better than the status quo** for the target user? The README
   roadmap already lists "remote API surface (trigger from phone / Slack / FluentBoards
   webhook)" as a *different* remote-access idea — is remote *runtime hosting* the same need
   or a distinct one? **Still open** — worth confirming the actual user story before
   building.

---

## 8. Rough phased outline (not a task list)

- **Phase 0 — spike (no product).** Manually stand up a VPS, `DOCKER_HOST=ssh` a single
  `ensure_instance` to prove the bind-mount breakage first-hand, then run the MCP server
  *on* the VPS over streamable-HTTP through Tailscale and confirm `fs_read`/`visit`/
  `wp_cli` work co-located. Goal: validate Model B's core claim (co-location makes the tool
  surface work unchanged) before writing any product code. Live-verify, in the spirit of
  `docs/ci-e2e-runner-spec.md` §3.8.
- **Phase 1 — single VPS, single dev, automated provisioning (§0).**
  `./sb remote add/provision/up`, provisioning fully scripted over SSH (Tailscale join +
  Docker + sandbox runtime + tools venv + deploy-target git repo); the `RuntimeBackend`
  seam (§4.1) + registry `runtime` field (§4.2, v2→v3 migration); streamable-HTTP
  transport with bearer auth bound to the Tailscale interface; `sb deploy` (git push +
  diff-apply, §0) in place of mutagen; `visit`/tools-venv provisioned server-side;
  artifact return (still open, §7 Q6). Release gate: *zero behavior change when no
  remote is configured.*
- **Phase 2 — durability & fit-and-finish.** Snapshot fetch-to-local / object-store backup;
  deploy-status UX (a visible "deployed as of commit X" indicator, §6); CI/e2e fan-out
  dispatched server-side end-to-end; `./sb doctor --remote`. (On-demand VPS start/stop
  is explicitly OUT of scope per §0 — the VPS is persistent and user-managed.)
- **Phase 3 (separate effort, gated on real demand) — multi-tenant / team VPS.** Per-user
  auth (mTLS or per-user tokens), per-project container/network isolation, resource quotas,
  secret isolation. This is effectively "we built a small Coder" and should be its own
  spec, not a continuation.

---

## Sources (external research)

- Docker Compose bind-mount limitations with remote hosts — [docker/compose #8484](https://github.com/docker/compose/issues/8484), [#11867](https://github.com/docker/compose/issues/11867), [Docker contexts docs](https://docs.docker.com/engine/manage-resources/contexts/)
- VS Code "Develop on a remote Docker host" (DOCKER_HOST-over-SSH vs. Remote-SSH+Dev Containers; SSH key auth required) — [code.visualstudio.com](https://code.visualstudio.com/remote/advancedcontainers/develop-remote-host), [Connect to remote Docker over SSH](https://code.visualstudio.com/docs/containers/ssh)
- MCP transports (stdio vs. streamable-HTTP; auth; Origin-header/DNS-rebinding guidance) — [MCP spec: Transports](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports), [Auth0: Why MCP's move to Streamable HTTP simplifies security](https://auth0.com/blog/mcp-streamable-http/), [Descope: Adding auth & remote support to a local MCP server](https://www.descope.com/blog/post/auth-remote-mcp)
- Remote-dev-environment architectures (Coder self-hosted agent model; Codespaces VM; DevPod client-only) — [vcluster comparison](https://www.vcluster.com/blog/comparing-coder-vs-codespaces-vs-gitpod-vs-devpod), [Coder compare](https://coder.com/solutions/workspaces/compare)
</content>
