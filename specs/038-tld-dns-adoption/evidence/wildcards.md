# Wildcard zone lifecycle (Ubuntu 24.04)

**Scope**: T055 — a declared wildcard capability yields ONE zone binding that answers
previously unseen subdomains, and the zone disappears with its final owner.

**Host**: Ubuntu 24.04.4 LTS, systemd-resolved with the scoped Sandbox authority.
Harness: `python3 tests/live_resolver_wildcard.py --project-dir ~/git/templately
--label tmp-logo --evidence-id 038-t055-ubuntu-2404`, with
`sandbox.config.override.json` declaring `{"domains": {"wildcard": true}}`. 2026-08-02.

## One zone, arbitrary subdomains

```text
apply     ready / ready     hostname templately-tmp-logo.test     answers ['127.0.0.77']

bindings  [{"kind": "zone", "name": "*.templately-tmp-logo.test", "owners": 1}]

lookups after a single cache flush:
  templately-tmp-logo.test                    -> 127.0.0.77
  unseen-sub.templately-tmp-logo.test         -> 127.0.0.77
  another-tmp-logo.templately-tmp-logo.test   -> 127.0.0.77
```

Neither subdomain existed when the zone was created, and no further mutation happened
between the lookups (FR-015, FR-016, SC-007). Exactly one binding covers all of them.

## Scope is the declared local suffix only

The binding name is `*.templately-tmp-logo.test` — one level below the suffix, never
`*.test`, so it cannot shadow another project or the incumbent's own `.test` names.

A publicly delegated name classifies as `public`, which the policy refuses to shadow
locally (FR-014):

```text
suffix_class("example.com", "test") -> "public"
```

## Removal with the final owner

```text
cleanup                         ready / cleanup_complete
lookup unseen-sub.<hostname>    (no answer)
```

## Not covered

- Two projects sharing one zone, where removing the first must retain the zone until the
  last owner is gone. The repository models shared owners (`release_binding_owner` returns
  `retained`) and unit coverage exists in `tests/test_domain_wildcard_lifecycle.py`, but
  the live two-project run is open.
- An exact-name-only resolver refusing subdomain multisite (FR-015 negative case).
