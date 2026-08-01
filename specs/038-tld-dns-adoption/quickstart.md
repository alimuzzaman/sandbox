# Quickstart: TLD and DNS Adoption Validation

## Safety baseline

Use a disposable supported host or VM. Record the active resolver owner, `/etc/resolv.conf`
relationship, selected internet/local/search-domain answers, listening DNS endpoints, and
the per-port Sandbox URL. Do not use raw Docker or direct resolver mutation; feature
lifecycle goes through `./sb`.

```bash
./sb ensure --project-dir . --json
./sb domains support --json
./sb domains status --project-dir . --json
./sb domains plan --project-dir . --json
```

Expected: observation/plan makes no host change, identifies the active resolver and pin
source, chooses `.test` only for a new unpinned identity, and includes the working per-port
fallback.

## Live systemd-resolved path

On Ubuntu 24.04 with resolved owning its normal stub symlink:

```bash
./sb domains apply --project-dir . --json
./sb domains status --project-dir . --json
./sb visit http://<assigned-name>.test
```

First apply must require an interactive review if consent/privilege is absent. After
acceptance, status must show a scoped route to the Sandbox authority, a fresh answer equal
to A's accepted listener address, and successful HTTP service. The original resolv.conf
symlink and unrelated sampled answers must be unchanged.

Run apply twice. The second run must report the same binding and create no duplicate route,
authority, process, or state record.

## Failure and collision proof

With isolated host fixtures or disposable VMs, exercise:

1. foreign authority endpoint collision;
2. foreign exact/zone resolver rule;
3. changed last-applied rule;
4. resolver owner change between plan and apply;
5. non-TTY first use;
6. stale cache returning the prior address;
7. new `.local`, existing `.tst`, and public FQDN identities;
8. exact-only resolver with subdomain multisite.

Every case must preserve foreign state and the per-port URL. Non-TTY execution must return
without prompting or mutation. Public FQDN tests must create zero local records.

## Cleanup proof

```bash
./sb domains cleanup --project-dir . --json
./sb domains cleanup --project-dir . --json
./sb domains status --project-dir . --json
```

Expected: only the unchanged owned binding is removed; the final shared-zone owner removes
the zone and stops the authority; a second cleanup converges. Drift or unavailable
incumbent state yields `cleanup_incomplete` with a non-secret retry record.

## Adapter release gate

For each manifest entry proposed as `adoptable`, run its host conformance scenario:

- fresh lookup then HTTP request through selected ingress;
- exact and wildcard behavior where declared;
- unrelated internet/local/search/VPN answer comparison;
- add/update/re-ensure/cleanup twice;
- foreign collision, drift, reload failure, and owner disappearance;
- non-interactive first-use behavior.

Attach the evidence identifier required by the manifest. Without it the adapter must remain
`implemented_unproven` or `detect_only` in `domain_support`.

