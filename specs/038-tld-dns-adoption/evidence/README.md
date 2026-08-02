# DNS adoption evidence index

The default Sandbox-owned strategy is proven live on macOS. No resolver adapter is
adoptable on any platform, so every ADOPTION artifact remains open — implementation tests
alone never promote support.

| Scenario | Evidence | Status | Still required |
|---|---|---|---|
| Default Sandbox-owned resolution | `default-strategy.md` | live (macOS) | Linux run |
| Persisted `.tst` and Compose fallback | `compatibility.md` | live (macOS) | Linux run |
| Read-only quickstart baseline | `quickstart-run.md` | partial (macOS) | adoption lifecycle section |
| Ubuntu 24.04 systemd-resolved exact name | `systemd-resolved.md` | live (Ubuntu 24.04) | — |
| Cleanup, drift, and repeated cleanup | `cleanup.md` | live (Ubuntu 24.04) | owner change (needs a second resolver manager) |
| Wildcard and shared-owner lifecycle | `wildcards.md` | pending live run | depends on the systemd-resolved run |

## What the open items need

T034 is captured: exact-name adoption, fresh lookup, HTTP through the ingress, repeat
apply, and cleanup all pass live on Ubuntu 24.04, and the run found and fixed five
defects that made adoption impossible on any real host. The remaining items —
owner-change/drift/unreachable cleanup (T050) and the wildcard zone lifecycle (T055) —
now have a working adapter to exercise; they need their own fixture runs.

## Rules for adding evidence here

Live proof must use `./sb`, include the before/after resolver owner and
`/etc/resolv.conf` relationship, compare unrelated answers, perform a fresh lookup plus
HTTP request, repeat apply/cleanup, and show that foreign state is unchanged. The live
harness may inject only the invocation-scoped typed proof attestation; no CLI/config value
can promote support, and the attestation does not alter advertised support. State plainly
what each file does not cover.
