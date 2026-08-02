# Incumbent native runtime evidence

Date: 2026-08-02
Host: macOS (`darwin`)
Branch: `latest`

## Read-only live result

After `./sb native support --project-dir . --json` confirmed the public support
declarations, the read-only acceptance harness ran:

```text
mcp/wp-server/.venv/bin/python tests/live_incumbent_acceptance.py
exit 0
```

Observed:

- Laravel Herd 1.29.0 was available and reported PHP 8.5.8.
- Herd status accepted an exact PHP 8.5 requirement, returned the declared
  document root, required a user-supplied database for `ensure`, labeled itself
  `trusted_shared_host`, and reported `route_mutations: false`.
- A user-authority declared POSIX profile resolved the absolute Herd PHP binary
  and repository document root, retained the user-supplied database reference,
  labeled itself `trusted_shared_host`, and reported `route_mutations: false`.
- Official Valet was not installed, so no Valet operation was claimed.
- The harness performed status/preflight observations only and reported
  `mutated: false`; it did not link, secure, unlink, edit DNS, or edit ingress.

## Adoption decision

This evidence deliberately does **not** make an incumbent adapter adoptable.
The runtime manifest remains fail-closed:

```text
herd            implemented_unproven  adoptable=false
valet           implemented_unproven  adoptable=false
declared-posix  conditional            adoptable=false
```

The host is a trusted shared runtime, not a containment boundary. Untrusted
plugins and CLI payloads must continue to use Compose or a live-proven managed
container; the incumbent adapters are not registered as selectable backends
until their full CLI/test/database lifecycle is independently proven.
