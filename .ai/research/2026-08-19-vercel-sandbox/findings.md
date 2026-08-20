# Research findings: what Vercel Sandbox does, and which of it applies to Sandbox

Date: 2026-08-19
Scope: official Vercel Sandbox documentation (product page, concepts, CLI
reference, pricing/limits), the `vercel/sandbox` repository skill, and the
2026-08-18 security-challenge announcement. Local claims about this repository
come from reading `./sb --help`, `./sb snapshot --help`, and a grep of the
Compose/config sources. No Vercel account was created and no Vercel API was
called; every remote statement is from published documentation.

The source catalog is beside this file in `source-index.json`.

## Executive conclusion

Vercel Sandbox and this Sandbox solve different problems — theirs is ephemeral
untrusted-code compute, ours is long-lived trusted WordPress development — so
the product shape does not transfer. Four of its *primitives* do, and three of
them line up with work already queued here:

1. **Sandbox identity is separate from the running VM.** A named sandbox holds
   config and filesystem; a "session" is one running instance of it. Stop ends
   the session, not the sandbox. This is the missing concept behind
   spec 043's storage-pressure work: today `./sb` can stop a stack (`down`) or
   destroy its data (`clean`), with nothing in between that survives reclamation.
2. **Persistence is the default and is automatic.** Filesystem snapshots on
   stop, resume from the newest snapshot on the next call. No operator ritual.
3. **Fork from a snapshot.** New isolated environment seeded from an existing
   one's current snapshot plus its config. Directly attacks our most expensive
   repeated operation: every `run_e2e` worker and every `ci_run` matrix cell
   installs WordPress from scratch.
4. **Snapshot retention is declared policy, not manual hygiene.** Expiration
   TTL, keep-last-N, and an explicit eviction rule are creation-time options.

What must not transfer is the security claim. Their isolation boundary is a
per-tenant Firecracker microVM with a dedicated guest kernel; ours is Docker on
a shared kernel, and on the remote host that kernel is shared across every
workspace. Vercel is currently paying up to $1M to have that boundary attacked.
Sandbox should keep describing itself as a trusted-code environment.

## 1. How Vercel Sandbox works

### Compute and isolation

- One [Firecracker](https://firecracker-microvm.github.io/) microVM per sandbox,
  with its own guest kernel, filesystem, network namespace, and process space.
  Root is available inside; `sudo` workloads (Docker-in-sandbox, VPN clients,
  FUSE mounts) are supported because the microVM, not the container, is the
  boundary.
- Boots from a Vercel Managed Image, a custom OCI image in Vercel Container
  Registry, or a saved snapshot. Default image carries Node LTS, Python, coding
  agents, common utilities.
- Single region (`iad1`). Startup is advertised in milliseconds; resume from a
  snapshot is faster than a cold create.

### Lifecycle vocabulary

| Term | Meaning |
| --- | --- |
| Sandbox | Durable, project-unique **name** plus config. Name is immutable after creation. |
| Session | One running VM instance of that sandbox. |
| Stop | Ends the session. Persistent sandboxes snapshot the filesystem first. |
| Resume | Implicit: the next SDK call on a stopped persistent sandbox starts a new session from the newest snapshot. |
| Remove | Permanent deletion of the sandbox and all of its sessions. |

Provisioning and rehydration are separate hooks: `onCreate` runs once (install
dependencies), `onResume` runs after an automatic resume (restart the dev
server). `Sandbox.getOrCreate({name})` is the idempotent entry point.

### Snapshots and forking

- Persistent sandboxes snapshot on every stop, so snapshots accumulate by
  design; retention is therefore a first-class creation option:
  `--snapshot-expiration <dur>` (default `30d`, `none`/`0` to disable),
  `--keep-last-snapshots <1..10>`, `--keep-last-snapshots-for <dur>`, and
  `--delete-evicted-snapshots <bool>` (default `true` — evicted snapshots are
  deleted immediately rather than left to expire).
- Snapshots expire 30 days after last use by default.
- `sandbox fork <source>` seeds a new sandbox from the source's current snapshot
  and copies its config; any flag passed overrides the copied value. `env` is
  **not** copied (it is encrypted server-side), and `--tag` fully replaces the
  source's tags instead of merging per key.

### Command surface

Deliberately shaped like the Docker CLI: `list|ls`, `create`, `run`, `exec`,
`connect|ssh|shell`, `copy|cp`, `stop`, `remove`, `fork`, `config`, `sessions`,
`snapshot`, `snapshots` (list/get/delete/tree), `drives`, `login`, `logout`.

- `sandbox run --name X -- <cmd>` is get-or-create plus exec; create-only flags
  are ignored when the sandbox already exists.
- `--stop` ends the session when the command exits; `--rm` deletes the sandbox.
  They are mutually exclusive.
- `--connect` opens an interactive shell straight after create/fork.
- Interactive sessions extend the timeout automatically unless
  `--no-extend-timeout` is passed.

### Networking

- Outbound is `allow-all` by default. Per-sandbox firewall policy:
  `--network-policy allow-all|deny-all`, `--allowed-domain <pattern>` (wildcards
  supported), `--allowed-cidr`, `--denied-cidr` (denies take precedence).
- Published ports (`--publish-port`, max 15) get a public URL.
- Because the firewall terminates TLS for transformation rules, each sandbox
  gets a unique proxy CA at `/etc/pki/ca-trust/source/anchors/vercel-proxy-ca.pem`
  and `/usr/local/share/ca-certificates/vercel-proxy-ca.pem`, added to the system
  trust bundle, plus a documented set of CA environment variables pointing at
  `/etc/ssl/certs/ca-certificates.crt`: `AWS_CA_BUNDLE`, `CARGO_HTTP_CAINFO`,
  `CURL_CA_BUNDLE`, `GIT_SSL_CAINFO`, `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH`,
  `NODE_EXTRA_CA_CERTS`, `NODE_USE_SYSTEM_CA`, `NPM_CONFIG_CAFILE`, `PIP_CERT`,
  `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`.
- Documented gotcha: a container run *inside* a sandbox does not inherit the CA
  or those variables, so its HTTPS calls fail verification until the certificate
  is mounted and added to the container's own trust store.

### Storage

- 32 GB ephemeral NVMe per sandbox on every plan.
- **Drives** (beta): a named persistent volume mounted into sandboxes with
  `--mount <drive>:<path>[:read-write|read-only]`, reusable across sandboxes.

### Other operator surface

- **Tags**: up to five `key=value` pairs, settable at create/fork, filterable in
  `ls` (`--tag env=staging`).
- Dashboard has Observability > Sandboxes with a Stop control.

### Limits and metering

| | Hobby | Pro | Enterprise |
| --- | --- | --- | --- |
| Max vCPUs / memory | 4 / 8 GB | 8 / 16 GB | 32 / 64 GB |
| Max duration | 45 min | 24 h | 24 h |
| Concurrent sandboxes | 10 | 10,000 | 10,000 |
| Open ports | 15 | 15 | 15 |
| Disk | 32 GB | 32 GB | 32 GB |

Default timeout is 5 minutes, extended with `timeout` / `extendTimeout()`.
Each vCPU carries 2 GB of memory.

Metered dimensions (Pro/Enterprise rates): Active CPU $0.128/hour — **time
waiting on I/O is not billed** — provisioned memory $0.0212/GB-hour billed in
1-minute minimums, creations $0.60 per million, egress $0.15/GB with inbound
downloads free, snapshot storage $0.08/GB-month. vCPU allocation is a *dynamic*
quota: it starts at the plan's floor, ramps with sustained use, and decays back
after 10 idle minutes. Control-plane calls and deletions have separate fixed
quotas.

### Security posture and the challenge

The 2026-08-18 announcement runs a HackerOne program from 18 August to
1 September 2026, or until the pool is exhausted, with up to $1M available and a
$50,000 maximum per report (critical $25k–$50k, high $10k–$25k, medium $5k–$10k,
low $1k–$5k). In scope: escaping the Firecracker microVM to the host or another
tenant, and bypassing the sandbox firewall to reach a disallowed destination.
Out of scope: escapes that only break out of the Linux container inside the
guest, because enforcement is at the host. Reports require a working
proof-of-concept through the `@vercel/sandbox` SDK; static analysis alone earns
nothing.

## 2. Where this Sandbox stands today

Verified locally on 2026-08-19:

- `./sb` lifecycle verbs are `up`, `down`, `clean`, plus `instance`/`workspace`
  operations. There is no "stop the session but keep the environment restorable
  through reclamation" state, and no automatic resume.
- `./sb snapshot` accepts `name`, `--force`, `--db-only`, `--instance`,
  `--label`. No expiration, no keep-last-N, no eviction policy. Snapshots grow
  without bound and fall in the class that spec 042's tiered cleanup refuses to
  touch.
- There is no `fork`. `run_e2e(workers=N)` and `ci_run` matrix cells each
  provision a fresh instance and install WordPress from scratch.
- Instances carry a derived name and an operator-chosen `label`; there is no
  free-form tag dimension for policy to select on.
- `NODE_EXTRA_CA_CERTS` is set on the **host** (`sandbox/commands/config_setup.py`,
  for local mkcert/Herd HTTPS). No CA-bundle environment variables are injected
  into instance containers, and no `update-ca-certificates` step appears in the
  Compose or config sources. Whether the Caddy CA reaches the WP container's
  trust store is unverified — it needs a probe before any claim either way.
- `sb resources status` attributes disk. There is no per-instance CPU, memory,
  egress, or snapshot-bytes signal for a scheduler to rank victims by.

## 3. Recommendations

Ranked, with the spec each belongs to.

1. **Introduce the session/instance split and hibernation** — spec 043. Reframe
   storage-pressure relief from "reap the instance" to "stop the session,
   snapshot it, resume transparently on the next `sb wp` / `visit` / MCP call."
   Pressure relief then stops costing the user their site, which removes the
   main reason to keep everything running.
2. **`sb fork <instance>`** — new spec. Seed an instance from another's current
   snapshot plus config. Biggest measurable win is E2E/CI fan-out; the second is
   "reproduce this bug on a copy of the affected site." Copy Vercel's two
   sharp edges: do not copy secrets into the fork, and make tag/port overrides
   replace rather than merge, so the result is predictable.
3. **Snapshot retention policy** — spec 042/043. TTL plus keep-last-N plus an
   explicit eviction rule, declared in config, enforced on capture. Turns an
   unbounded, un-reclaimable class into a self-limiting one.
4. **Confirm the drive/store shape for spec 044.** Vercel's Drives — one named
   volume, many consumers, explicit `read-only` at the mount site — is
   independent validation of the shared node store and git dedup design. Adopt
   the explicit per-mount mode; it is what makes a shared store safe to attach
   widely.
5. **Per-instance metering.** Even with no billing, "active CPU, memory-hours,
   egress bytes, snapshot bytes, last-touched" is exactly the input a pressure
   scheduler needs to choose what to hibernate. Their separation of active CPU
   from I/O wait is the right definition of "idle" for our reaper too.
6. **Do not reap what is attached.** Their interactive sessions auto-extend the
   timeout. Our idle reaper should treat a live shell, an open browser session,
   or a running job as a lease.
7. **Verify and, if needed, fix container-side CA trust.** Probe whether an
   instance's `wp_remote_get()` to its own HTTPS domain succeeds. If it does
   not, Vercel's environment-variable list is a ready-made fix checklist.
8. **Tags.** Free-form `key=value` on instances, filterable in `instances`/`ls`,
   so retention and cleanup policy can target a set instead of matching names.
9. **Optional, only if the threat model changes: per-instance egress policy.**
   Relevant only if Sandbox ever runs untrusted plugin code or agent-generated
   code. Not needed for today's trusted-code workload.

## 4. Explicitly not adopted

- Ephemeral-first defaults, the 45-minute/24-hour session caps, and single-region
  placement. Our instances are long-lived development sites.
- Any claim of untrusted-code safety. Docker on a shared kernel is not a
  microVM boundary, and on the remote host that kernel is shared across
  workspaces. Sandbox documentation should keep saying trusted code only unless
  and until per-workspace VM isolation exists.
