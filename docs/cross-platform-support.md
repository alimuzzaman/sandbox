# Cross-platform support — Linux (Ubuntu/Arch/Fedora/etc), macOS, Windows

## Host ingress adoption matrix

Ingress detection is read-only on every platform and uses listener/process evidence only.
The table below is a support declaration, not permission to mutate a host product; no row is
advertised as adoptable until its complete live lifecycle evidence is accepted.

| Product family | Linux/macOS | Windows-side product | Initial tier | Route behavior |
|---|---|---|---|---|
| system Caddy exact HTTP | Linux | n/a | unit-gated live candidate | requires explicit scoped helper install; live evidence still pending |
| Sandbox Caddy, Herd/Valet, nginx, Apache, Traefik, Caddy HTTPS/wildcard | declared where detected | n/a | implemented-unproven | no mutation before proof |
| Nginx Proxy Manager | declared where detected | n/a | credential-pending | returns a redacted pending result |
| DDEV, Local, XAMPP | detect-only where publicly observable | n/a | detect-only | never reads private control state or mutates |
| Laragon, WAMP | outside host platform | Windows | outside-platform | never mutates from Linux/macOS |
| Unknown listener | listener truth only | listener truth only | unidentified | preserves per-port fallback |

An exact loopback listener can coexist with a dedicated Sandbox loopback listener; IPv4/IPv6
wildcards are conflicts only when their bind scopes overlap. HTTP and HTTPS are never split
across incumbent products for one hostname. See [host-ingress.md](host-ingress.md) for
transaction, consent, cleanup, and proof requirements.

## Current native WordPress runtime matrix

This document also covers host portability of the default Compose workflow. The newer
WordPress native-runtime contract is narrower and evidence-gated:

| Adapter | Host | Isolation | Current rule |
|---|---|---|---|
| Compose | supported Docker hosts | container runtime | default and adoptable |
| Ubuntu nspawn | normally booted Ubuntu 24.04, systemd 255+ | managed container | adoptable only when `native support` carries live evidence |
| Herd | Linux/macOS where official Herd CLI is available | trusted shared host | explicit opt-in, user database, no route ownership |
| official Valet | macOS | trusted shared host | explicit opt-in, user database, no route ownership |
| declared POSIX | Linux/macOS | trusted shared host | explicit user authority/profile only |
| Local/XAMPP/Laragon/WAMP | product-specific | none | detect-only |

Windows native execution remains unsupported; WSL2 can use the Linux Compose path, but it
does not satisfy the initial managed Ubuntu proof matrix merely by identifying as Linux.
Managed-native requires effective cgroup delegation, AppArmor, seccomp, private namespaces,
default-deny nftables, and a normally booted systemd host. See
[native-runtime-isolation.md](native-runtime-isolation.md).

Author: drafted 2026-07-09 (design-fidelity-diff session). Status: audited, real gaps
found and fixed, including a full working Linux implementation of the clean-URL HTTPS
proxy (§4), live-verified end-to-end. One narrower piece (systemd-resolved/NetworkManager-
managed systems) intentionally left as a documented follow-up rather than a guessed,
unverified port — see §4.

> Current adoption status: the newer project-scoped resolver service supersedes
> broad takeover as adapters earn live evidence. `./sb domains support --json`
> is authoritative: implemented-but-unproven adapters do not mutate. Linux
> systemd-resolved/NetworkManager, direct dnsmasq, exact hosts, macOS
> `/etc/resolver`, Herd/Valet, WSL2, and unknown managers each report a distinct
> tier. WSL2/external/unknown are read-only. The historical section below is
> retained as evidence, not as the current support advertisement.

## 1. Where this started

Before this session, the codebase already had MORE Linux awareness than a from-scratch
audit would expect: `scripts/install-ubuntu.sh` (apt → Docker CE, including the docker
group / systemd daemon-start dance), `_docker.py`'s prereq checker already distinguished
"daemon down" from "user not in the docker group" on Linux, and `_is_server()` already
auto-detects a headless (non-macOS, no `claude` CLI, no tty) box for a server-appropriate
UX. This audit's job was to find what's still missing, not build Linux support from zero.

## 2. What was verified genuinely broken (not guessed) — and fixed

Every fix below was checked against a REAL container image for the target distro before
being written — Docker images (`ubuntu:24.04`, `fedora:latest`, `archlinux:latest`, the
latter needing `--platform linux/amd64` since there's no arm64 Arch image, and
`DisableSandbox` in `pacman.conf` to work around a pacman-under-QEMU-emulation seccomp
limitation unrelated to the fix itself) — not assumed from package-name memory.

1. **Package manager detection was apt/dnf/brew-only** (`_pkg_manager()` in
   `sandbox/core/_ui.py`) — Arch (`pacman`) and openSUSE (`zypper`) fell through to
   `(None, None)`, meaning every prereq-install offer silently had nothing to suggest on
   those distros. Added both. `_docker.py`'s prereq checker and Arch's install script
   (§3) both use the extended detection now.

2. **`./sb domains setup` / `./sb secure` (clean HTTP(S) URLs) hardcoded `brew` as the
   ONLY way to install `mkcert`** (`proxy_setup()` in `sandbox/core/_domains.py`) — on any
   non-Homebrew machine (essentially all of Linux) this printed "Homebrew not found" and
   gave up, even though mkcert has perfectly good native packages elsewhere. Verified live:
   - `apt-get install mkcert` — works on Ubuntu 24.04 (universe repo), but does NOT pull in
     `libnss3-tools` (needed for `certutil`, mkcert's own Linux requirement for the
     OS/browser trust store) as a dependency — must be requested alongside it explicitly.
   - `dnf install mkcert` — works on Fedora, and DOES pull in `nss-tools` transitively on
     its own (confirmed: `nss-tools-0:3.125.0-...` appeared in the Fedora install
     transaction unprompted).
   - `pacman -S mkcert nss` — both are official Arch `extra` repo packages; `nss` is not an
     automatic pacman dependency of `mkcert` either, so both are requested explicitly
     (mirrors the original brew command's own "mkcert nss" pairing).
   Fixed to build the right command per detected package manager, with `certutil`
   confirmed present after install in every case tested.

3. **`./sb domains setup` would have CRASHED on Linux, not just degraded** — deeper audit
   than the mkcert install step: the entire clean-URL feature (`_ensure_url_proxy` →
   `tools/proxy-helper.sh`) was macOS-specific all the way down: `ifconfig lo0 alias` (Linux
   loopback is `lo`, and modern distros favor `ip addr`; more importantly Linux doesn't
   need loopback aliasing at all — the whole 127.0.0.0/8 range is already usable there,
   unlike macOS which restricts `lo0` to `127.0.0.1` by default), `/etc/resolver/<tld>` (a
   macOS-only wildcard-DNS mechanism with no Linux equivalent path), a macOS LaunchDaemon
   (`_install_alias_launchd`, `sandbox/core/_integ.py`), and macOS's own DNS cache
   (`dscacheutil`/`mDNSResponder`). None of this was gated — it would have hit
   `ifconfig: command not found` (or worse, silently done nothing useful) the first time a
   Linux user ran `./sb domains setup`. A first pass fixed this by detecting
   `sys.platform != "darwin"` and declining cleanly rather than crashing — but a REAL
   working Linux implementation was then built and live-verified later the same session
   (prompted by direct user request to push further rather than settle for a documented
   gap); see §4 for the full design, safety boundaries, and the real bugs found building it.

4. **Two separate `launchctl` calls would `FileNotFoundError` (not just fail cleanly) on
   Linux** — `launchctl` doesn't exist as a binary at all outside macOS, so calling it
   raises `FileNotFoundError` regardless of `check=False`/`capture_output` (those only
   govern behavior around a nonzero EXIT CODE, not a missing executable) — a real crash
   risk, not a graceful degrade, in two places neither of which was gated:
   - `proxy_teardown()`'s `launchctl unload` (`sandbox/core/_domains.py`) — reachable via
     `./sb uninstall` even on a machine where the clean-URL feature was never set up.
   - `_configure_node_extra_ca()`'s `launchctl setenv NODE_EXTRA_CA_CERTS`
     (`sandbox/commands/config_setup.py`) — part of wiring GUI MCP clients to trust a local
     CA. Fixed by gating both to `sys.platform == "darwin"`; the Linux path for the second
     one still writes the cert bundle and sets `os.environ` for the current process, with a
     printed `export NODE_EXTRA_CA_CERTS=...` line for the user's own shell rc (Linux GUI
     apps typically inherit session/shell env more readily than macOS Dock-launched apps
     do, so this is a reasonable, honest substitute rather than a silent no-op).

5. **`act`'s Docker networking (CI runner, `docs/ci-e2e-runner-spec.md` §3.6)** — already
   correct for BOTH platforms without change: `host.docker.internal` + explicit
   `--add-host=host.docker.internal:host-gateway` is needed on Linux (where Docker doesn't
   auto-provide that hostname the way Docker Desktop does) but harmless to also pass on
   macOS — no platform branch needed, already portable.

6. **`sandbox/core/_asyncjobs.py`'s `setsid`** — fixed in the earlier CI/e2e work this
   session (`docs/ci-e2e-runner-spec.md` §4.3): the external `setsid` BINARY doesn't exist
   on macOS at all (util-linux is Linux-only), so shelling out to it unconditionally would
   have been the INVERSE bug (Linux-only code breaking macOS). Fixed by relying on
   `start_new_session=True`, which makes Python call `setsid()` itself on any POSIX
   platform — this one fix is what makes that module correct on BOTH, not just Linux.

## 3. New: Arch Linux bootstrap script

`scripts/install-arch.sh` — mirrors `scripts/install-ubuntu.sh`'s shape (python check →
Docker check → hand off to `install.sh`), adapted for pacman. Notable differences,
verified live rather than assumed:
- Arch's `python` package bundles `venv` — unlike Debian/Ubuntu's split `python3-venv`
  package, there's no separate venv package to install; a missing venv module on Arch
  means python3 itself is missing, not a split-package gap.
- `docker` AND `docker-compose` (the v2 plugin, confirmed via `docker compose version`
  after install — not the deprecated standalone `docker-compose` binary) are both official
  Arch `extra` repo packages — no third-party APT-style repo dance needed, unlike Ubuntu's
  `download.docker.com` GPG-key setup.

README.md updated to list all three (macOS/Ubuntu/Arch) install scripts, note that
Fedora/openSUSE work via `./sb setup`'s own dnf/zypper detection without a dedicated
one-shot script yet, and clarify Windows guidance (§5).

## 4. Linux clean-URL HTTPS — implemented and live-verified

**Update, same session, after further live testing (prompted by direct user request to
verify more thoroughly rather than leave this as a documented gap):** a real, working Linux
implementation now exists in `tools/proxy-helper.sh`, live-verified against a fresh Ubuntu
24.04 container for every action (`alias-up`/`alias-down`/`dns-up`/`dns-down`/`dns-flush`),
including a full multi-TLD lifecycle (two TLDs added, one removed while the other survives,
then the last one removed, restoring the machine's original resolvers) and confirming real
internet domains keep resolving throughout (upstream forwarding, not a takeover).

**Design.** No lo0-style alias is needed at all — Linux's loopback interface already routes
the entire `127.0.0.0/8` range with zero setup (verified: `nc -l 127.0.0.77` accepted a
connection with no prior `ip addr add`), so `alias-up`/`alias-down` are no-ops on Linux.
For DNS, `tools/proxy-helper.sh` runs its OWN `dnsmasq` instance bound to `127.0.0.1`,
serving `address=/.<tld>/127.0.0.77` wildcard rules from a sandbox-owned `--conf-dir`
(supporting several TLDs at once, same as macOS), forwarding everything else to the
machine's real upstream resolvers (captured once, before ever touching
`/etc/resolv.conf`), then points `/etc/resolv.conf` at itself. `sandbox/core/_domains.py`'s
`_ensure_url_proxy` calls into this exactly the same way it does on macOS — no platform
branch needed there anymore beyond the `_lo0_alias_present`/`_resolver_present` marker
checks, which now read the right on-disk marker per OS.

**Safety boundaries (proxy-helper.sh declines cleanly, exit 3, rather than guess):**
- `/etc/resolv.conf` must be a **plain regular file**. A symlink almost always means
  `systemd-resolved` or NetworkManager manages DNS on this box — fighting either (revert
  races, DBus-based reconfiguration) was not something that could be verified safely
  without a real desktop Linux environment (a nested-Docker systemd+DBus test hit a real
  wall: `systemd-resolved` started but couldn't reach the system bus — `sd_bus_open_system:
  Connection refused` — a container-networking artifact, not evidence the approach is
  wrong on a real machine, but not something to guess past either). On such systems, the
  proxy declines and the plain `http://localhost:<port>` fallback keeps working.
- Port `127.0.0.1:53` must be free, or already held by OUR OWN previously-started dnsmasq
  (checked by PID liveness + `/proc/<pid>/comm`, not by parsing `ss`'s process-owner
  column — see the bug below). Never steals a port some other resolver already owns.

**Real bugs found and fixed via this live testing** (the reason live testing — not just
"the design looks right" — mattered here too):
1. `SIGHUP` does **not** reliably pick up a file newly ADDED to dnsmasq's `--conf-dir`
   after its initial start — verified: adding a second TLD via `SIGHUP`-reload left the new
   TLD's wildcard silently unresolved until a full process restart. Fixed by always doing a
   full kill+relaunch (not `SIGHUP`) whenever `dns-up`/`dns-down` change which TLD files
   exist; `SIGHUP` is still correct — and still used — for `dns-flush` (a pure cache-clear
   with no file-set change).
2. The "is port 53 already ours" safety check initially parsed `ss -lntpu`'s process-owner
   column to compare against our tracked pidfile — but that column doesn't reliably appear
   in every container/kernel config (verified: a real bound listener showed with NO
   `users:(...)` field at all), and the parser then misread the unrelated `0.0.0.0:*`
   peer-address field as if it were a PID, causing a false-positive rejection when adding a
   second TLD to an already-running instance. Fixed to check PID liveness +
   `/proc/<pid>/comm` instead of relying on `ss` to report ownership.
3. **Investigation dead-end worth recording so it isn't re-chased**: a test that removed
   one of two active TLDs appeared to still resolve the removed one. Traced conclusively —
   by querying Docker Desktop's own internal DNS relay (`192.168.65.7` on this Mac)
   *directly*, bypassing the sandbox's dnsmasq entirely, and getting the identical stale
   answer for a domain name that had never been queried before — to Docker Desktop's OWN
   internal DNS relay caching a stale answer from earlier in the SAME test session, a
   nested-Docker-Desktop-for-Mac testing artifact with nothing to do with
   `tools/proxy-helper.sh`, which was already behaving correctly (its own logs showed it
   correctly NOT matching the removed TLD locally and correctly forwarding upstream — the
   wrong answer came from outside the script entirely).

**Still out of scope, by design, not oversight:** systemd-resolved/NetworkManager-managed
systems (common on desktop Ubuntu/Fedora with a symlinked `/etc/resolv.conf`) still decline
cleanly rather than attempt `resolvectl domain`/NetworkManager's dnsmasq plugin — those
paths could not be verified without a real desktop Linux environment (see the DBus wall
above), and shipping an unverified DNS-touching implementation risks a feature that LOOKS
like it works but silently doesn't, which is worse than an honest decline. The plain HTTP
fallback (`http://localhost:<port>`) already works identically on every platform regardless
— this whole section only affects the OPT-IN clean-URL upgrade, never core functionality
(provisioning, wp-cli, e2e/CI runners, MCP tools — none of that touches this code path).

## 5. Windows

Not attempted natively, and not realistically attemptable without a much larger rewrite:
`./sb` is a `#!/bin/sh` polyglot bootstrap (Python's own parser skips the leading shell
block via a docstring trick) — cmd.exe/PowerShell don't understand shebangs or POSIX shell
syntax at all, so the entry point itself doesn't run. Beyond the entry point, the codebase
also assumes: Docker's Unix domain socket (not the Windows named-pipe transport), POSIX
process groups + `SIGTERM`/`os.killpg` for job control (`sandbox/core/_asyncjobs.py`,
`sandbox/commands/jobs.py`), and `chmod`/POSIX file permissions (secrets files, `sb`
itself). None of this maps cleanly onto native Windows.

**Recommendation: WSL2**, where the tool behaves EXACTLY like the Ubuntu path in §2-3 —
WSL2 is a real Linux kernel + userspace, not an emulation layer, so nothing here is
Windows-specific work at all; it is fully covered by the Linux fixes already in this doc.
Docker Desktop's WSL2 backend (or Docker Engine installed directly inside the WSL2
distro) both work. This was not separately live-tested (no Windows/WSL2 machine available
in this environment) but there is no code path here that behaves differently under WSL2
vs. a native Ubuntu install — the kernel-level POSIX guarantees WSL2 provides are exactly
what this tool already relies on.

## 6. What did NOT need any change (verified already portable)

- The Docker Compose stack itself (wp/nginx/db/mailpit containers) — always Linux
  containers regardless of HOST os; nothing here is host-platform-sensitive.
- `act`'s `host.docker.internal` networking (§2 item 5).
- MCP client registration — shells out to the `claude` CLI itself (`shutil.which("claude")`
  + `claude mcp add ...`), which handles its own cross-platform config location; the
  sandbox code never hand-writes a Claude Desktop config path itself.
- `mcp/wp-server/` — no macOS-specific code found anywhere in the MCP server's own tool
  implementations.
- Herd support — inherently macOS-only (Laravel Herd is a native Mac app), but this was
  ALREADY correctly scoped as pure opt-in: a project must explicitly set
  `"server": "herd"` in `sandbox.config.json` to touch any Herd code path at all. A Linux
  user never triggers it by default. No gate needed — it's already gated by the fact that
  nobody sets that config value on a machine without Herd installed.

## 7. Test coverage

No NEW unit tests were added specifically for platform-detection branches (they're mostly
`sys.platform`/`shutil.which` conditionals around subprocess calls to real system tools —
the meaningful verification is "does the real command work on the real distro," which unit
tests with mocks can't actually prove, and which was instead done via the live container
checks in §2). The full existing suite (166 tests) stayed green throughout. If this
warrants dedicated tests later, the highest-value ones would mock `_pkg_manager()`'s return
value and assert `proxy_setup`'s install_cmd selection per manager — cheap to add, low
signal (they'd test a dict literal, not real distro behavior).
