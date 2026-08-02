# DNS adoption evidence index

No resolver adapter is advertised as adoptable from implementation tests alone.

| Scenario | Evidence | Status |
|---|---|---|
| Ubuntu 24.04 systemd-resolved exact name | `systemd-resolved.md` | pending live run |
| Cleanup, drift, and repeated cleanup | `cleanup.md` | pending live run |
| Wildcard and shared-owner lifecycle | `wildcards.md` | pending live run |
| Persisted `.tst` and Compose fallback | `compatibility.md` | pending live run |

Live proof must use `./sb`, include the before/after resolver owner and
`/etc/resolv.conf` relationship, compare unrelated answers, perform a fresh lookup plus
HTTP request, repeat apply/cleanup, and show that foreign state is unchanged. The live
harness may inject only the invocation-scoped typed proof attestation; no CLI/config value
can promote support, and the attestation does not alter advertised support.
