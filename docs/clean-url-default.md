# Clean-URL default: Docker + Caddy

Sandbox's own Docker/Caddy proxy plus Sandbox-owned DNS is the **default** clean-URL
provider on every platform and for every runtime. Host-incumbent adoption (specs 037/038)
and native runtimes (spec 039) are **opt-in alternatives**, never the implicit default.

This page is the contract. Anyone continuing specs 037/038/039 must read it before
touching `sandbox/core/_domains.py`, `tools/proxy-helper.sh`, or the ingress/resolver
manifests.

## The rule

- Default provider: `sandbox-caddy` — the Caddy container on `127.0.0.77`, plus the
  loopback alias and the `*.<tld>` resolver/dnsmasq entries the helper installs.
- Adapter support tiers (`implemented_unproven`, `adoptable`, …) gate **adoption only**.
  They MUST NOT gate the default path: with zero proven adapters, clean URLs still work.
- Per-port `http://localhost:<port>` is reported only when the default provider itself is
  unavailable (no Docker, a foreign listener on the required endpoints, declined sudo).
  `sb ensure` then says so, and when `http://<domain>:<port>` answers on this machine it
  names that as an alternative. It is only offered, never adopted, because the name
  resolves only where Sandbox DNS is installed.
- Docker Compose stays the default runtime. Selecting Herd/Valet or managed-native is the
  explicit opt-in that hands ingress to that product.

Spec anchors: 037 FR-007, FR-031, FR-032, FR-033, FR-034 · 038 FR-029 – FR-033 ·
039 FR-041, FR-042 · constitution principle VI.

## Why the proxy exists and when it runs

Each local Docker instance publishes its own port (`http://localhost:8188`). The proxy
(`sandbox-proxy`, one `caddy:2-alpine` container on `127.0.0.77:80/443`) is what lets a
browser use `http://<instance>.<tld>` without a port: the helper points `*.<tld>` at
`127.0.0.77`, and Caddy forwards each hostname to its instance's port. It routes nothing
else, so it has work to do only while a local Docker instance runs (spec 082):

- The container has `restart: "no"`. An engine restart (Docker Desktop, OrbStack, a
  reboot) does not bring it back, the same as the instances themselves. Older installs
  created it with `restart: unless-stopped`; the next proxy apply rewrites the compose
  file and recreates the container with the new policy.
- `sb up` starts a stopped proxy for an instance with a `.<tld>` domain, once the host
  is already provisioned (loopback alias and resolver present). That needs no sudo and
  never reloads a proxy that is already running. `sb ensure --create` and
  `sb domains up|setup` start it as before.
- `sb down` and `sb instance delete` then check demand and stop the proxy when no
  `sandbox-*` Compose project has a running container and no idle-stopped instance has a
  wake-on-request route. Anything the check cannot read (engine error, timeout, an
  unreadable catalog) keeps the proxy running, and so does a start running at the same
  time (a shared lock on `runtime/proxy/.on-demand.lock`). Rerunning the check is a
  no-op. An adopted ingress is never stopped by it.
- While the proxy is stopped, instance URLs fall back to `http://localhost:<port>`.
- `domains.ingress: disabled` means no clean URLs: lifecycle never starts the proxy, and
  the check treats it as having no demand.

## Remote-only and Docker-less controllers

A controller that only drives remote servers, or has no container engine, can say so:

```yaml
# $SANDBOX_HOME/sandbox.local.yml
controller:
  local_runtime: none     # docker (default) | none
```

`SANDBOX_LOCAL_RUNTIME=none|docker` overrides it for one process; an unknown value is an
error, never a silent Docker. With `none`:

- `sb ensure`, `sb up`, `sb down`, `sb wp` and every other local Docker instance command
  refuse before running `docker`, with a message naming the switch.
- Herd instances keep working, and the Docker proxy is never started.
- `sb instances` and `sb web` list Docker instances as not running without asking the
  engine.
- `sb doctor` needs no local instance. It reports `local runtime: none (remote-only)`,
  then remote targets and storage, and a missing engine is not a problem.
- Remote work (`sb job-start --remote`, `sb host apply`, `sb host stack`, `sb remote`,
  `sb resources --remote`) is unchanged. None of it needs local Docker, switch or not;
  the exception is `sb host apply --local-images`, which builds images on the controller
  by design.

An old proxy started before the switch was set stops with `sb domains down` and then
stays stopped. Without the switch, a machine with no `docker` binary gets a clean
refusal from local instance commands, and doctor reports `Docker CLI available` failing
instead of crashing.

## Switching provider

```bash
./sb domains use                 # report the current provider
./sb domains use sandbox-caddy   # back to the default (removes the machine-local pin)
./sb domains use herd-valet      # opt in to an incumbent, no reprovisioning
./sb domains setup [tld]         # apply the selection (adds trusted HTTPS)
```

Precedence: `SANDBOX_CLEAN_URL_MODE` env → machine-local `domains.ingress` in
`sandbox.local.yml` → project `domains.ingress` in `sandbox.config.json` → default
`sandbox-caddy`. Switching preserves the persisted hostname and never reprovisions.

## Privilege model

The default path needs two host actions: a loopback alias and a `*.<tld>` resolver entry.
Both live in `tools/proxy-helper.sh`, and the NOPASSWD rule never names the checkout:

1. `sudo tools/proxy-helper.sh install` copies the script to a root-owned
   `/usr/local/libexec/sandbox-proxy-helper` and writes
   `/etc/sudoers.d/sandbox-proxy-<uid>` allowing only that path and its fixed verbs.
2. Every privileged verb refuses to run from any other path (`require_installed_helper`),
   so a stale rule pointing at a writable checkout is inert.
3. The pre-2026-08 rule at `/etc/sudoers.d/sandbox-proxy` is revoked on first setup.

This is the fix for the escalation risk that motivated the 2026-08-02 retirement — the
answer was to move the privileged target out of the checkout, not to remove clean URLs.

## Diagnosing a dead clean URL

`./sb doctor`, a non-ready `./sb domains status`, and
`./sb domains ingress status --json` report whether the proxy's
published endpoints actually accept connections, not merely that Docker reports a
mapping. A container runtime can widen a published
`127.0.0.77:80` bind to a wildcard one and then lose that port to whatever already owns it
(observed live: OrbStack with `docker.expose_ports_to_lan: true` versus Herd's nginx on
`127.0.0.1:80`). When doctor can identify the owner, it names the owning process and points
at the two recoveries: free the port, or `./sb domains use <provider>` to route through the
incumbent instead. If no owner is identified, doctor does not guess or advise stopping an
arbitrary service. It first checks the `127.0.0.77` loopback alias without changing it: a
missing alias is a host prerequisite and points to `./sb domains setup`; when the alias is
present, it points to the supported `./sb domains up` proxy recovery. The per-port fallback
remains available in either case.

Ingress status also probes the current project's exact Caddy hostname and generated
route. When Sandbox owns the published sockets but that route is missing or does not
reach Sandbox Caddy, status is `degraded` and returns the same reason code used by
ensure. Unrelated stale hostnames do not fail an ensure for a newly created route.

`sb ensure` also retries the exact clean route for an already-ready Compose instance
whose recorded URL still points at `localhost`. This recovery is instance-scoped: it
does not assign domains to unrelated registry entries, and it returns localhost only
when the default provider remains unavailable after that retry.
Fresh creation also retries that route after WordPress installation and HTTP readiness,
because an empty document root cannot satisfy the initial route proof. A successful
retry repairs WordPress's canonical URL before returning the instance.
Wildcard or probable listener evidence is reported as overlap; `listener_conflict`
requires an exact bind plus proven ownership evidence.

If Caddy logs `forward_auth host.docker.internal:8766` with a connection refusal,
the activation authority is down while an older wake-route Caddyfile is still loaded.
`./sb domains up` now regenerates direct routes when that authority is unhealthy, so a
running WordPress container remains reachable and a stopped one fails at its backend
port instead of every request failing at the dead gateway. `./sb activation status`
shows whether the authority is active.

Generated activation-gateway and backend routes use an explicit three-second HTTP dial
timeout and allow up to two additional upstream attempts within an eight-second retry
window, with 250 ms between attempts. This gives transient `host.docker.internal` dial
failures another chance while bounding the extra wait when an instance or the activation
gateway is unavailable. Each upstream also caps open, dialing, and idle connections at
64 per host. This lets the reported 49-asset admin page avoid queuing behind an
eight-connection limit while still bounding host-gateway dials. The retry and connection
cap are resilience measures; they do not identify or repair an underlying Docker
host-gateway problem.
Exact single-host route probes wait nine seconds: the eight-second Caddy retry window plus
one second to receive and inspect its response. This keeps `site_url()` and scoped ingress
status from declaring a route unavailable while Caddy is still retrying. Whole-fleet route
scans keep their shorter per-route budget.
Activation request header names are matched case-insensitively. Caddy or the host HTTP
server may canonicalize `X-Sandbox-Route-ID` to `X-Sandbox-Route-Id`; both spellings
refer to the same HTTP field and must authorize the same registered route. The long-lived
activation authority caches its immutable route catalog against the config and registry
source-file metadata, so unchanged asset requests do not reload YAML/JSON or republish
route policies. A source change triggers a full validated rebuild. Read, validation, or
concurrent-change failures revoke cached routes and fail closed until a stable refresh
succeeds.
The wake adapter resolves WordPress instance state through `sandbox.core`; it does not
depend on the retired `sandbox_core` compatibility namespace for runtime ownership.
It reloads current instance config for each wake. Backend readiness uses the owned
localhost port without following redirects: probing the canonical route during wake
would re-enter the same pending forward-auth request. Public route acceptance remains
a separate check in the lifecycle smoke.
On macOS, the supervised activation service records the discovered Docker CLI directory
in its bounded PATH so an OrbStack-backed wake can run outside an interactive shell.
Exact-route creation allows one bounded five-second activation proof; broad managed-route
health scans retain the short per-route probe budget.
When a clean base URL is already recorded, `sb ensure` also repairs stale login and admin
companion URLs so callers never receive a mixed `.tst`/`localhost` record.
Before accepting the advertised route, ensure checks both WordPress `home` and `siteurl`;
if either differs, it updates and reads back both options so a stale redirect cannot survive
because only `siteurl` matched.

OrbStack's automatic container DNS is a separate engine feature: names such as
`<compose-service>.<project>.orb.local` are generated by OrbStack, not by `sb`. Sandbox
clean URLs remain `<instance>.tst` (or `http://localhost:<port>` when its proxy is not
available); an `.orb.local` link should not be persisted as the instance's Sandbox URL.
An old mkcert file is not proof that an instance is secured: `sb` only enables the Caddy
HTTPS redirect when the registry explicitly records an `https://` URL. This prevents a
stale certificate from sending ordinary HTTP `.tst` traffic into a host runtime's
intercepted 443 listener.

## Do not re-retire this path

The Docker/Caddy path was disabled in place on 2026-08-02 (`_ensure_url_proxy` stubbed to
return `False`, `tools/proxy-helper.sh` replaced by a refusal). Effect: every instance on
macOS silently fell back to `http://localhost:<port>`. The replacement adoption path had
no usable Darwin adapter, so it could not provide parity with the cross-platform default.

Linux system-Caddy adoption now has one source-owned production qualification: exact HTTP
only, with a supported system executable and a read-only helper preflight that binds the
observed PID, start time, socket inode, and listen endpoint to the active
`caddy.service` MainPID before DNS may change. A same-name process, a second Caddy owning
the selected socket, or a binary below `/tmp` or `/home` is not qualified. Normal live
Linux CLI adoption through this path has not yet been recaptured, so Spec 037 T078 remains
open. This narrow opt-in path does not change the Docker/Caddy default.

Under constitution principle VI, disabling this path in place counts as removal. It
requires recorded live parity of the replacement plus explicit human approval. Guard tests
live in `tests/test_clean_url_default_policy.py`; they fail if the helper becomes a stub
again, if the NOPASSWD target moves back into the repo, or if the default mode changes.
When clean routing is unavailable, Sandbox installs a generated loopback MU
plugin. It keeps `home_url()` unchanged for the browser, while cURL requests to
that exact localhost origin or the instance's clean `.tst` hostname first use
the instance's private `nginx` service with the original `Host` header. This
avoids routing container self-requests through a host port that may belong to
OrbStack or another host service. When the instance has no resolvable `nginx`
service, cURL falls back to `host.docker.internal`. This allows
`wp_remote_get( home_url() )` without making a public or LAN address canonical.
Other ports and hostnames are not rewritten.
