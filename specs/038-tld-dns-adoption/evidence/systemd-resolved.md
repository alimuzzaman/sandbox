# systemd-resolved exact-name adoption (Ubuntu 24.04)

**Scope**: T034 — live scoped adoption through systemd-resolved: ownership, unrelated
answers, fresh lookup, HTTP through the selected ingress, repeat apply, and cleanup.

**Host**: Ubuntu 24.04.4 LTS, systemd 255, x86_64. systemd-resolved owns
`/etc/resolv.conf` through its stub symlink; system Caddy owns `:80`/`:443`; Docker and
several unrelated services are running. Project `~/git/templately`, label `tmp-logo`,
instance URL `http://localhost:8188`. 2026-08-02.

**Historical harness**: `python3 tests/live_resolver_acceptance.py --project-dir ~/git/templately
--label tmp-logo --consent --evidence-id 038-t034-ubuntu-2404`. The typed attestation is
constructed inside the harness for that single invocation. `--consent` records that the
operator approved the first mutation of this machine's resolver; the run was authorized by
the repository owner for this purpose.

The current source-owned qualification uses this evidence ID as a constraint but keeps
ordinary support `implemented_unproven` with no advertised evidence ID. It
accepts no attestation, CLI/config/environment value, or other runtime promotion input. It
is Linux-only and exact-name-only. Before mutation, a read-only installed-helper preflight
must bind the observed resolved owner to the active service PID, process start identity,
owner UID, and systemd control group. Local tests cover missing helpers, unsupported
platforms/capabilities, changed and second owners, foreign state, and mutation ordering.
This file does not yet contain a fresh normal `./sb domains use systemd-resolved` run, so
T067 remains open.

## Promotion is proof-gated, not configuration-gated

```text
historical harness without attestation:  systemd-resolved adoptable = False
historical harness with attestation:     systemd-resolved adoptable = True
current source-owned composition:        implemented_unproven, adoptable = False
```

Nothing on the CLI or in configuration can widen the current fixed qualification.

## Lifecycle

```text
status_before      fallback     resolver_not_selected   mutated=False  owner=none
plan               pending_consent consent_required     mutated=False  owner=none
apply_first        ready        ready                   mutated=True   owner=owned
status_after_apply ready        ready                   mutated=False  owner=owned   answers=['127.0.0.77']
apply_second       ready        shared_binding_joined   mutated=True   owner=shared
                                "Joined an existing healthy owned resolver binding."
cleanup_first      ready        cleanup_complete        mutated=True   owner=none
cleanup_second     ready        already_absent          mutated=False  owner=none
```

Fresh lookup after a cache flush, and a request through the selected ingress:

```text
getent hosts templately-tmp-logo.test  ->  127.0.0.77      templately-tmp-logo.test
curl http://templately-tmp-logo.test/  ->  200
```

While adopted, the host showed the scoped route and nothing wider:

```text
resolvectl domain   Global: ~test
resolvectl dns      Global: 127.0.0.55:44489      Link 2 (eth0): 195.179.224.53 209.126.15.53
drop-in             /etc/systemd/resolved.conf.d/80-sandbox-test.conf
                    # sandbox-resolver v1 suffix=test
                    [Resolve]
                    DNS=127.0.0.55:44489
                    Domains=~test
authority           /usr/sbin/dnsmasq --conf-file=<sandbox home>/runtime/network/authority/dnsmasq.conf
```

## Foreign state unchanged

```text
/etc/resolv.conf  before: ../run/systemd/resolve/stub-resolv.conf
                  after : ../run/systemd/resolve/stub-resolv.conf   (unchanged symlink)

unrelated answers before: example.com 2606:4700:10::ac42:93f3, 2606:4700:10::6814:179a
                          github.com  140.82.121.3
unrelated answers after : identical
```

The link's own upstream servers were never touched, no global resolver mode changed, and
the machine's stub listeners (`127.0.0.53`, `127.0.0.54`) were left alone.

## After cleanup

```text
resolvectl domain            Global: (none)
pgrep dnsmasq                (no authority process)
getent hosts <name>.test     (no answer)
/var/lib/sandbox/resolver/{applied,authorizations}/   empty
```

## Defects this run found and fixed

1. **dnsmasq argv** — the authority launched `--conf-file <path>`; dnsmasq accepts only
   `--conf-file=<path>` and rejects the separated form with "junk found in command line".
   Every authority start failed, reported as `authority_endpoint_changed`.
2. **Authority endpoint** — the allocator defaulted to `127.0.0.54`, systemd-resolved's own
   DNS-proxy stub. resolved installs the routing domain but silently declines to use that
   address as an upstream, so lookups NXDOMAINed while a direct `dig` to the authority
   answered correctly. Default moved to `127.0.0.55`; both stub addresses are now refused.
3. **Ownership fingerprint** — the "did the resolver change between plan and apply" guard
   compared a digest that includes current answers and raw `resolvectl status` text. Two
   consecutive observations of an untouched host already disagreed (unrelated containers
   add veth interfaces), so the second apply failed as `resolver_changed`. Now compared on
   a stable ownership digest.
4. **Helper variable scoping** — `require_root_directory` assigned `owner` from a
   directory's uid while the calling verb held the project's owner digest in that same
   POSIX-sh global, so every receipt was written under owner `0`. Receipts became
   unattributable and cleanup could never find its own receipt.
5. **Malformed neighbour receipts** — the shared-receipt scan hard-failed on any receipt it
   could not parse, which made removal impossible once (4) had written a bad one.

Each fix has unit coverage; see the commits referencing this file.

## Not covered

- A fresh normal `./sb domains use systemd-resolved` production-composition run (T067).
- Wildcard zone lifecycle (T055) and the drift/unreachable cleanup cases (T050 remainder).
- NetworkManager, standalone dnsmasq, and Herd/Valet resolver paths.
- A host where `/etc/resolv.conf` is a plain file rather than the resolved stub.
