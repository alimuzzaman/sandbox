# DNS adoption evidence index

The default Sandbox-owned strategy is proven live on macOS. No resolver adapter is
adoptable on any platform, so every ADOPTION artifact remains open — implementation tests
alone never promote support.

| Scenario | Evidence | Status | Still required |
|---|---|---|---|
| Default Sandbox-owned resolution | `default-strategy.md` | live (macOS) | Linux run |
| Persisted `.tst` and Compose fallback | `compatibility.md` | live (macOS) | Linux run |
| Read-only quickstart baseline | `quickstart-run.md` | partial (macOS) | adoption lifecycle section |
| Ubuntu 24.04 systemd-resolved exact name | `systemd-resolved.md` | pending live run | a Linux host running systemd-resolved |
| Cleanup, drift, and repeated cleanup | `cleanup.md` | partial (macOS) | an owned binding to clean up |
| Wildcard and shared-owner lifecycle | `wildcards.md` | pending live run | depends on the systemd-resolved run |

## What the open items need

`systemd-resolved.md` (T034) is the gate: `ResolverProofAttestation` accepts only the
`systemd-resolved` adapter, so it is the single adapter that can ever reach `adoptable`
today, and it exists only on Linux. The cleanup remainder (T050) and the wildcard
lifecycle (T055) both need a binding Sandbox owns, which requires that run first.

## Rules for adding evidence here

Live proof must use `./sb`, include the before/after resolver owner and
`/etc/resolv.conf` relationship, compare unrelated answers, perform a fresh lookup plus
HTTP request, repeat apply/cleanup, and show that foreign state is unchanged. The live
harness may inject only the invocation-scoped typed proof attestation; no CLI/config value
can promote support, and the attestation does not alter advertised support. State plainly
what each file does not cover.
