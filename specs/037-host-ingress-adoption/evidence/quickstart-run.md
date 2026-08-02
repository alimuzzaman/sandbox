# Quickstart run (macOS, partial)

**Scope**: T068 — `specs/037-host-ingress-adoption/quickstart.md` executed end to end
through `./sb` on darwin. The read-only baseline, bind-scope observation, and clean-URL
sections ran; the live system-Caddy lifecycle section did not, because it needs a Linux
host with an adoptable system Caddy.

**Host**: macOS 15 (Darwin 25.6.0), project `templately-staging`. 2026-08-02.

## Read-only baseline

```text
./sb ensure --project-dir .          -> templately-staging  https://templately-staging.tst  ready
./sb domains support --json          -> ok, 13 adapters, adoptable: none
./sb domains status --project-dir .  -> state=fallback hostname=templately-staging.tst
                                        reason=resolver_not_selected mutated=False
./sb domains plan --project-dir .    -> state=unsupported reason=resolver_not_adoptable mutated=False
./sb domains apply --project-dir .   -> state=unsupported reason=resolver_not_adoptable mutated=False
```

`apply` is included deliberately: with no adoptable adapter it must refuse before any
mutation rather than partially applying. It did, and reported `mutated=False`.

Listener, route, and resolver state were identical before and after the sequence — see
`listeners.md` for the captured diff.

## The clean URL serves through the default provider

```text
./sb visit https://templately-staging.tst/
  status: 200
  title:  HomeHymn – Real Estate Consultancy Service Template for Elem…
```

Headless Chromium rendered the page over HTTPS, which also proves the local CA is trusted
(Chromium refuses an untrusted certificate). The 7 console errors are page-level Elementor
messages, unrelated to ingress.

## Repeat safety

```text
domains ingress cleanup   x2  -> ok already_absent mutated=false (identical both runs)
domains ingress reconcile     -> ok already_absent residual=[]
domains cleanup           x2  -> ok already_absent mutated=false (identical both runs)
```

## Command bounds

```text
domains status   0.25s     domains detect        0.21s
domains plan     0.21s     domains support       0.21s
ingress detect   0.27s     ingress plan/support  0.21s
```

All well inside the 2-second read-only and 3-second planning bounds.

## Not run

- **Live incumbent lifecycle** (`domains apply` through a real system Caddy, request
  through the incumbent, repeat apply, cleanup twice): needs the T044 host.
- **Transaction failure proof** against a real incumbent (invalid current config, invalid
  candidate, reload failure, health failure, foreign collision, incumbent disappearance).
  Unit coverage exists in `tests/test_ingress_transactions.py`; the live half is open.
