# Research: Full authenticated source implementation

Prepared through speckit-plan Phase 0. No network, secret values/metadata or runtime inspection.

| Decision | Source evidence and rationale | Rejected alternative |
| --- | --- | --- |
| Retain actual gate receipt | tailnet_access.py ControlGate.admit returns token or verified tailnet login/node; server.py middleware discards result | Client actor, fabricated human token provenance, asynchronous access log as authentication |
| Accepted installation late port | User supplied exact binding.py contract and broad owner policy; lifecycle owns durable IDs in both layouts | Owner uid/home context as stable identity; 086 initializing IDs; manual registration repair |
| Extend job repository | sandbox/jobs/registry.py owns transactions and durable identity; hosting/recovery owns target state/fences | Independent JSON job/secret ledger; context-only delivery admission as effect grant |
| Broker callback plus fixed manifest | SecretService and GHCRStagingCredentialAdapter show fixed revision-bound consumption precedent | Arbitrary get-value API, generic argv consumer or weakened escalation guard |
| Confidential app adapter | hosting.py writes runtime environment.env and uses compose env-file; required TYPESAFE_API_KEY in supplied comparable template currently takes that route | Store provider credentials in compose/env or claim legacy app compatibility from declared names |
| Full source now | User explicitly expanded disabled slice to real implementation | Future-only TODO packet or unconditional source refusal as complete solution |

Unknown installed facts remain unknown: current installed revision/capability, registered metadata and source revisions, actual sandbox-env/gs artifact/mappings, supported app provider configuration and original GS reservation/effects. Do not probe or retry them in this work package. No source evidence resolves these live facts. Installation owner's accepted source port resolves design ownership, not installation proof.

Independent expanded readiness/design review is pending with root. Historical independent PASS and helper reports belong only to their original packet. Preserve original logs; earlier 27 failures were not rerun by this design task.
