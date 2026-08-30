# DNS adoption evidence index

The default Sandbox-owned strategy is proven live on macOS. The Ubuntu
systemd-resolved lifecycle is captured through the historical single-invocation
conformance harness. Source-owned code now constrains only that exact Linux
systemd-resolved candidate, but ordinary support remains `implemented_unproven` until the
normal CLI path is rerun live.

| Scenario | Evidence | Status | Still required |
|---|---|---|---|
| Default Sandbox-owned resolution | `default-strategy.md` | live (macOS) | Linux run |
| Persisted `.tst` and Compose fallback | `compatibility.md` | live (macOS) | Linux run |
| Read-only quickstart baseline | `quickstart-run.md` | partial (macOS) | adoption lifecycle section |
| Ubuntu 24.04 systemd-resolved exact name | `systemd-resolved.md` | live (historical harness); implemented locally, unadvertised | normal `./sb domains use systemd-resolved` live run |
| Cleanup, drift, and repeated cleanup | `cleanup.md` | live (Ubuntu 24.04) | owner change (needs a second resolver manager) |
| Wildcard and shared-owner lifecycle | `wildcards.md` | live (Ubuntu 24.04) | the two-project shared-owner run |

## What the open items need

T034 is captured in the historical harness: exact-name adoption, fresh lookup, HTTP through the
ingress, repeat apply, and cleanup all pass live on Ubuntu 24.04, and the run found and
fixed five defects that made adoption impossible on any real host. T067's locally testable
portion is now source-owned and cannot be widened by runtime proof input. It requires a
read-only installed-helper preflight bound to the observed active service PID, start
identity, owner UID, and control group before endpoint or DNS mutation. T067 remains open
until the normal CLI path is rerun on Linux. The remaining items —
owner-change/drift/unreachable cleanup (T050) and the wildcard zone lifecycle (T055) —
now have a working adapter to exercise; they need their own fixture runs.

## Rules for adding evidence here

Live proof must use `./sb`, include the before/after resolver owner and
`/etc/resolv.conf` relationship, compare unrelated answers, perform a fresh lookup plus
HTTP request, repeat apply/cleanup, and show that foreign state is unchanged. The
historical harness attestation is evidence context only; current production composition
accepts no attestation or other runtime promotion input. State plainly what each file does
not cover.
