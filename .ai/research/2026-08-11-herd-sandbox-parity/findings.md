# Research findings: Herd-equivalent Laravel and Node environments in Sandbox

Date: 2026-08-11
Scope: read-only local inspection plus official Herd, Laravel, Docker, Node.js,
Next.js, pnpm, PHP-image, and MySQL-image documentation. No `.env` value was
opened, printed, copied, or persisted.

Machine-readable evidence is beside this file in `local-evidence.json`; the
official source catalog is `source-index.json`.

## Executive conclusion

Sandbox can reproduce the **observable application contract** of the current
Laravel + MySQL + Next.js setup through its supported generic Compose runtime,
but it cannot honestly claim to run “exactly as Herd.” Herd is a native macOS
stack built around its own PHP binaries, nginx, dnsmasq, Node tooling, `.test`
routing, and optional managed services. Sandbox Compose runs Linux containers,
publishes through Sandbox-owned loopback/Caddy behavior, and has different
filesystem, process, networking, and extension provenance.

Three product modes are materially different:

| Mode | What it preserves | What it changes | Current support |
| --- | --- | --- | --- |
| Pinned Compose parity | application versions, commands, dependency services, ports, health, tests, persistent data, repeatability | macOS native binaries, nginx/dnsmasq path, filesystem/watch performance | supported and adoptable |
| Hybrid host/container | native frontend performance plus containerized backend/database | one lifecycle spans unmanaged host and managed container processes | not modeled safely |
| Generic incumbent Herd | Herd PHP/nginx/Node/services themselves | lower isolation and shared-host side effects | WordPress-only, implemented-unproven, non-adoptable |

The recommended first product is pinned Compose parity with an explicit report
of every known difference. Generic Herd execution should be a separate future
decision rather than a hidden fallback.

## 1. What the reference environment actually is

The reference is not a single Herd-owned stack:

- Backend: Laravel 12.59.0, Composer PHP constraint `^8.3`, observed Herd PHP
  8.4.23. The observed web process used PHP's built-in server, not Herd nginx.
- Database: DBngin MySQL 8.0.27, independently managed from Herd.
- Frontend: Next.js 16.2.12 with Turbopack, Node 22.18.0 from NVM,
  `packageManager: pnpm@11.5.2`; the observed global pnpm was 11.17.0.
- Neither application repository contains a Compose file or Sandbox descriptor.

This distinction matters: reproducing the current behavior means reproducing
three independently owned components and their connection contract. Matching
Herd marketing architecture is not the same acceptance target.

## 2. What Sandbox already provides

- One framework-neutral Compose adapter supports explicit PHP, Node, Laravel,
  Astro, and similar projects with ensure, status, start, stop, logs, bounded
  argv execution, apply, destroy, and open capabilities.
- Sandbox supplies a loopback-only port overlay, resource limits, registry
  identity, bounded HTTP readiness, and non-destructive volume behavior.
- A project may declare a preferred host port, startup timeout, forced
  recreation, resource bounds, and named test modes.
- Compose is the only relevant runtime currently marked adoptable. The Herd
  adapter is `implemented_unproven`, `adoptable=false`, and registered for
  WordPress rather than generic projects.
- Registered `.env*` sources and the secret broker already prevent ordinary
  agent workflows from opening a whole secret file. Bounded use deliberately
  passes one selected key to one direct-argv child; it is not an application
  environment loader.

## 3. Confirmed product gaps

### 3.1 The initializer advertises more than it generates

`sb init --type` accepts Laravel and Node labels, but only Astro has a preset.
Without an existing Compose file, Laravel/Node initialization stops with a
clear refusal. This is safe, yet it makes the advertised labels recognition
aliases rather than useful guided initialization.

The current Astro preset also writes files and proceeds into ensure in one
command. The pre-existing product specification says generated configuration
should be reviewable before execution. New presets should not deepen that
review/execute ambiguity.

### 3.2 “Exact” requires a parity vocabulary

A useful report needs at least four states per fact:

- **matched**: exact observed value or digest is the same;
- **compatible**: declared constraints are satisfied but the value differs;
- **mismatched**: a declared or captured requirement is violated;
- **unverified**: evidence could not be collected without execution, access, or
  disclosure that was not authorized.

The overall result must never collapse compatible or unverified facts into
“exact.” Runtime version, patch version, extension list, INI behavior, OS/libc,
architecture, package manager, database mode/collation, routing, and file-watch
behavior are separate dimensions.

### 3.3 Application environment delivery is not secret inspection

Docker distinguishes Compose interpolation from variables passed into a
container. A project-root `.env` may be consumed for `${...}` substitution
without automatically becoming the application's environment. `env_file` can
pass a full file, while Compose secrets can grant selected file values to
specific services.

Neither option is automatically compatible with Laravel/Next conventions:

- Laravel normally loads a project `.env` itself when source is mounted, but
  container-only overrides such as `DB_HOST=mysql` still need a declared layer.
- Next.js has build-time, browser-exposed, and server-only variables; blindly
  delivering every key to every phase increases disclosure and can change the
  built artifact.
- The Sandbox broker accepts only owner-controlled, non-group/world-accessible
  registered files. The observed backend mode `0755` and frontend mode `0644`
  are therefore ineligible until an operator makes them owner-only.

The product needs a registered application-environment policy, not a loop that
calls one-key `secrets run` repeatedly and not a raw `env_file: .env` default.

### 3.4 Related projects need owned connectivity

Separate Compose projects receive separate default networks. Docker supports an
external shared network, but that network must exist before either project and
its ownership/cleanup must be explicit. The safe shape is:

- backend application joins a shared relation network and a backend-private
  network;
- database joins only the backend-private network;
- frontend joins the relation network and resolves the backend by stable alias;
- browser-visible URLs remain host/Caddy URLs, while server-side frontend calls
  use service discovery;
- deleting either instance cannot delete the other project or the shared
  relation while an owner remains.

This relation must not create an implicit global instance. Each project keeps
its canonical registry identity; a relation is explicit metadata with bounded
membership and ownership.

### 3.5 Readiness must include dependency health

Compose starts dependencies named by `depends_on`, but short syntax does not
wait for health. MySQL's official image explicitly warns that a fresh instance
does not accept connections until initialization completes. A Laravel preset
therefore needs database health as part of the observable readiness contract,
not merely an HTTP retry that hides repeated application connection failures.

### 3.6 Apple Silicon makes exact MySQL parity a real tradeoff

Registry manifest evidence shows:

- `php:8.4.23-cli` has a native `linux/arm64` manifest;
- `node:22.18.0-bookworm-slim` has a native `linux/arm64` manifest;
- `mysql:8.0.27` has only `linux/amd64`.

On this ARM64 Mac, exact MySQL 8.0.27 means emulation. A currently supported
native ARM64 MySQL image changes the database version. The product cannot decide
silently between version fidelity and native performance; it should report the
choice and later validate it with an application-relevant latency benchmark.

### 3.7 Containerized Next.js is compatible but not performance-equivalent

Next.js officially supports Docker but recommends native local development on
macOS and Windows for better development performance. Docker Desktop mediates
bind mounts through a Linux VM; source mounts are writable by default. A Node
preset can be reproducible and functionally correct while still losing the
native edit/reload latency users associate with Herd/NVM.

The acceptance gate must therefore measure cold start, warm edit-to-visible,
and server-component backend-call behavior instead of assuming Docker parity.
Debian/glibc is the safer default proposal than Alpine/musl for a project with
unknown native Node dependencies.

## 4. Recommended product boundary

One backlog phase should deliver:

1. A read-only parity capture/comparison vocabulary and report.
2. Reviewable Laravel and Node proposals based only on inert manifests and
   explicit user choices.
3. Explicit related-project metadata with owned cross-project discovery and
   private dependency networks.
4. Registered, least-disclosure application environment delivery.
5. Dependency-aware health, safe port-conflict refusal, bounded diagnostics,
   and measured live acceptance on Apple Silicon.

It should not claim byte-for-byte Herd equivalence, enable the non-adoptable
Herd adapter, mutate `.env` automatically, import an existing database without
a separate backup/restore contract, stop host processes implicitly, or publish
and deploy either application.

## 5. Evidence-to-requirement trace

| Evidence | Product consequence |
| --- | --- |
| Herd documents PHP/TLS/service versions in `herd.yml` | parity baseline must be explicit and shareable |
| Herd is native macOS with nginx/dnsmasq and `.test` | Compose result must disclose platform/routing differences |
| Laravel Sail models PHP + MySQL with service-name discovery | Laravel proposal can stay on the existing Compose adapter |
| Compose health conditions wait for database readiness | readiness should include declared dependency health |
| Separate Compose projects need an external network | related-project network needs explicit ownership |
| Compose interpolation differs from container env | environment delivery must be modeled, not assumed |
| Compose secrets are service-granular | secret delivery should grant only declared consumers |
| Next recommends native dev on Mac/Windows | performance parity needs measurement and may fail |
| Node Alpine uses musl | do not infer Alpine when native dependencies are unknown |
| pnpm 11 supports Node 22 and projects can pin versions | Node and package-manager evidence should be separate facts |
| MySQL 8.0.27 is amd64-only in registry | exact DB pin versus native ARM is an owner decision |
| Sandbox Herd support is non-adoptable | generic native Herd execution is not an implementation shortcut |

## 6. Research cautions

- Port occupancy changed during the research window; it is transient evidence,
  not a durable project fact. Re-probe immediately before any apply.
- Both reference repositories are ahead of upstream, and the backend contains
  concurrent uncommitted work. Generated project files must be reviewed and
  staged without touching unrelated changes.
- Image tags can move or disappear even when a digest remains resolvable. A
  later specification must decide whether the product records tags, digests, or
  both; this research does not make that supply-chain decision.
- No database contents, credentials, `.env` values, or external service calls
  were inspected. Data compatibility and authenticated flows remain unverified.
