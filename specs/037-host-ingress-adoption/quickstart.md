# Quickstart: Host Ingress Adoption Validation

## Read-only baseline

Use a disposable host or a host whose incumbent route lifecycle is authorized. Ensure a
real Sandbox project first; the tooling repository itself intentionally has no implicit
instance.

```bash
./sb ensure --project-dir /path/to/project --json
./sb domains support --json
./sb domains status --project-dir /path/to/project --json
./sb domains plan --project-dir /path/to/project --json
```

Record all relevant listener addresses/ports, incumbent identity/config health, and a
bounded sample of existing routes. Status/plan must not change listeners, files, service
state, or routes.

## Bind-scope conformance

Exercise isolated fixtures for:

- free `127.0.0.77:80` with foreign `127.0.0.1:80` (coexistence);
- foreign `0.0.0.0:80` (conflict);
- IPv6 exact and `::` with effective dual-stack state;
- Sandbox-owned exact listeners;
- HTTP-only, HTTPS-only, and split-product ownership;
- hidden process identity with visible kernel listener.

Every classification must preserve listeners. Split ownership must never produce one
advertised hostname.

## Live system Caddy lifecycle

The current Ubuntu host provides a conformance target: system Caddy owns `*:80`/`*:443`,
runs from `/etc/caddy/Caddyfile`, and already imports `/etc/caddy/conf.d/*.caddy`.

```bash
./sb domains apply --project-dir /path/to/project --json
./sb domains status --project-dir /path/to/project --json
./sb visit http://<verified-hostname>
./sb domains apply --project-dir /path/to/project --json
./sb domains cleanup --project-dir /path/to/project --json
./sb domains cleanup --project-dir /path/to/project --json
```

Expected: first use reviews consent/privilege interactively; Sandbox's proxy does not
start; one owned fragment appears through the detected include surface; complete config is
validated as the service identity; the hostname serves the backend; pre-existing route
samples remain healthy; repeat apply/cleanup converge.

## Transaction failure proof

For every configuration adapter, inject or create disposable cases for invalid current
config, invalid candidate, reload failure, new-route health failure, baseline-route health
failure, foreign hostname collision, changed owned route, and incumbent disappearance.
The exact prior owned state must be restored where possible, foreign/drifted state must be
byte-for-byte unchanged, and the per-port URL must remain usable.

## Support-tier release gate

Run live add → request → update → request → remove for every proposed adoptable adapter,
including its declared HTTP/HTTPS/wildcard capabilities and unrelated route samples. An
adapter without its manifest evidence identifier remains `implemented_unproven` or
`detect_only`; detection alone cannot promote it.

Verify Nginx Proxy Manager, DDEV, Local, XAMPP, Laragon/WAMP under WSL2, and unidentified
listeners emit their named limitation and perform zero mutation.

