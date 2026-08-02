# Quickstart run (macOS, partial)

**Scope**: T060 — `specs/038-tld-dns-adoption/quickstart.md` executed through `./sb` on
darwin. The safety baseline ran in full; the live systemd-resolved path and the collision
fixtures did not, because both require a Linux host.

**Host**: macOS 15 (Darwin 25.6.0), project `templately-staging`. 2026-08-02.

## Safety baseline

Recorded before the sequence:

```text
resolver owner    macos:scoped-resolver (tier implemented_unproven)
/etc/resolv.conf  symlink -> ../var/run/resolv.conf   (macOS-managed, untouched)
/etc/resolver/    test (Herd's), tst (Sandbox's)
per-port URL      http://localhost:8188
```

Sequence and results:

```text
./sb ensure --project-dir .          -> templately-staging  https://templately-staging.tst  ready
./sb domains support --json          -> ok; adoptable: none
./sb domains status --project-dir .  -> state=fallback hostname=templately-staging.tst
                                        resolver=macos:scoped-resolver
                                        reason=resolver_not_selected
                                        fallback=https://templately-staging.tst  mutated=False
./sb domains plan --project-dir .    -> state=unsupported reason=resolver_not_adoptable mutated=False
```

Expected properties, all observed:

- observation and planning changed nothing on the host;
- the active resolver owner and the pin source are identified;
- the persisted `.tst` identity is reported rather than a synthesized `.test` name — this
  was wrong until the fix recorded in `compatibility.md`;
- the working fallback URL is included in every result.

Unrelated answers before and after were identical (`example.com` still resolved through the
machine's upstreams), and `/etc/resolv.conf` was unchanged.

## Repeat safety

```text
./sb domains cleanup --project-dir .   x2
  -> ok state=ready reason=already_absent mutated=false, identical both runs
```

## Read-only bounds

```text
domains status 0.25s   detect 0.21s   plan 0.21s   support 0.21s
```

Inside the 2-second read-only bound.

## Not run

- **Live systemd-resolved path** (apply, scoped route to the Sandbox authority, fresh
  lookup, HTTP service, second apply with no duplicate state). Needs Ubuntu with resolved
  owning its stub symlink; `ResolverProofAttestation` accepts only that adapter, so this is
  the gate for the whole adoption path.
- **Failure and collision fixtures** (foreign authority endpoint, foreign exact/zone rule,
  changed last-applied rule, unreachable resolver during cleanup). Unit coverage exists in
  `tests/test_domain_cleanup.py` and `tests/test_domain_incumbent_adapters.py`; the live
  half is open.
