# System Caddy exact-HTTP unit gate

**Scope**: simulated and unprivileged validation only. This is not live Caddy lifecycle
evidence and does not complete T044.

Validated properties:

- a foreign overlapping listener prevents Sandbox Caddy selection;
- accepted addresses are concrete and served on every required protocol;
- an unavailable installed-helper preflight prevents selection;
- system Caddy advertises exact HTTP only; HTTPS and wildcard remain unavailable;
- foreign-route baseline requirements cannot pass with zero samples;
- privileged candidate preparation binds the root-owned staged copy to the approved digest,
  owner, hostname, backend, and route ID;
- helper installation records one exact UID/network root and excludes `install` from its
  passwordless fixed-verb grant.
- the production registry qualifies only Linux system Caddy exact HTTP, bound to
  `037-t044-ubuntu-2404`, with no runtime proof input;
- changed or unidentified process owners, unproven sockets, foreign collisions, Darwin,
  HTTPS, wildcard hostname capability, and missing/failed helper readiness fail closed.

Focused command:

```text
mcp/wp-server/.venv/bin/python -m unittest \
  tests.test_ingress_qualification \
  tests.test_ingress_selection tests.test_ingress_registry \
  tests.test_ingress_file_adapters tests.test_ingress_verification \
  tests.test_ingress_helper tests.test_ingress_caddy
```

Live status: **PENDING**.
