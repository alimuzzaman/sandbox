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
- Docker Compose stays the default runtime. Selecting Herd/Valet or managed-native is the
  explicit opt-in that hands ingress to that product.

Spec anchors: 037 FR-007, FR-031, FR-032, FR-033, FR-034 · 038 FR-029 – FR-033 ·
039 FR-041, FR-042 · constitution principle VI.

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

`./sb doctor` asserts the proxy's published endpoints actually accept connections, not
merely that Docker reports a mapping. A container runtime can widen a published
`127.0.0.77:80` bind to a wildcard one and then lose that port to whatever already owns it
(observed live: OrbStack with `docker.expose_ports_to_lan: true` versus Herd's nginx on
`127.0.0.1:80`). When doctor can identify the owner, it names the owning process and points
at the two recoveries: free the port, or `./sb domains use <provider>` to route through the
incumbent instead. If no owner is identified, doctor does not guess or advise stopping an
arbitrary service; it points to the supported `./sb domains up` recovery and keeps the
per-port fallback available.

## Do not re-retire this path

The Docker/Caddy path was disabled in place on 2026-08-02 (`_ensure_url_proxy` stubbed to
return `False`, `tools/proxy-helper.sh` replaced by a refusal). Effect: every instance on
macOS silently fell back to `http://localhost:<port>`, because no resolver or ingress
adapter can reach `adoptable` on darwin — `ResolverProofAttestation` only accepts
`systemd-resolved` and `IngressProofAttestation` only accepts `system-caddy`.

Under constitution principle VI, disabling this path in place counts as removal. It
requires recorded live parity of the replacement plus explicit human approval. Guard tests
live in `tests/test_clean_url_default_policy.py`; they fail if the helper becomes a stub
again, if the NOPASSWD target moves back into the repo, or if the default mode changes.
