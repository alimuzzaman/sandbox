# Quickstart: validate edge and DNS transactions

Use a disposable test zone and remote. Production zones (xspeed-hub, Lenzora,
alimuzzaman.me) need owner approval. Provider tokens reach the CLI only
through the registered secret source; never print them.

1. **Unit, contract and parity tests**:
   `.cli-venv/bin/python -m unittest tests.test_edge_txn_desired tests.test_edge_txn_ownership tests.test_edge_txn_journal tests.test_edge_txn_executor tests.test_edge_txn_verify tests.test_edge_txn_provision tests.test_edge_txn_hosting_parity tests.test_architecture_boundaries`
   Expected: OK.
2. **Plan refusal**: add a read-only or foreign-marked record on a declared
   test hostname, then run `./sb host plan ... --json`. Expected:
   `would_refuse` with the record and reason. `host apply` makes zero
   provider changes (compare the zone listing before and after).
3. **IPv4-only move**: on a target with owned A and AAAA, apply to a remote
   without IPv6. Expected: the zone listing shows the A at the new origin and
   no Sandbox AAAA.
4. **Injected failure**: with the fake provider failing the third change
   (test hook), expected: the first two records are restored field for
   field, `rollback_complete`.
5. **Crash**: kill the controller after two journaled changes. Expected: the
   next `host plan` lists the interrupted transaction, and apply refuses until
   it is adopted with `--adopt-records` or cleaned up.
6. **Propagation**: on a fresh proxied hostname with basic auth, apply.
   Expected: success, and evidence shows per-edge-address attempts and the
   budget used. With the resolver pinned to the origin
   (`/etc/hosts` override), proxied verification still reports
   `source: authoritative` addresses only.
7. **Adoption after upgrade**: on a zone with legacy-comment records, the
   first plan lists every one as `unmarked`. Apply without `--adopt-records`
   changes nothing; one `--adopt-records` adopts them all.
8. **Proxied control endpoint**: provision a test remote whose proxied
   control hostname has browser-integrity enabled. Expected:
   `control_endpoint_refused_by_proxy` with the offered modes, and never
   `reachable`.
9. **nginx parity**: repeat steps 4 and 6 on an nginx front-door remote.
   Expected: identical per-record results.
