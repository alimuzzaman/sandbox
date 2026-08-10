# Native runtime adoption evidence index

The runtime manifest is the promotion authority. Evidence files record observed
commands and do not make an adapter adoptable by themselves.

| Evidence | Host | Status | Promotion effect |
|---|---|---|---|
| `pre-live-gate.md` | Ubuntu 24.04 | complete | managed runtime remains blocked |
| `isolation-prerequisites.md` | Ubuntu 24.04 | complete | none: 19/19 gates pass; promotion still needs T047 |
| `managed-provisioning.md` | Ubuntu 24.04 | superseded | provisioning now completes end to end; see `ubuntu-nginx.md` |
| `capability-parity.md` | macOS + contract suite | complete | none |
| `bounds-and-suites.md` | macOS Compose | complete | none (T076 suites + timing bounds) |
| `compose-regression.md` | macOS Compose | complete | preserves existing Compose adoption |
| `incumbents.md` | macOS Herd/POSIX; Valet absent | complete for available hosts | incumbents remain unadoptable |
| `ubuntu-nginx.md` | Ubuntu 24.04 | partial | provisioning, isolation and lifecycle proven; hostile matrix and exhaustion open |
| `ubuntu-apache.md` | Ubuntu 24.04 | partial | provisions alongside nginx; hostile matrix, grants and exhaustion open |
| `ubuntu-package-coexistence.md` | Ubuntu 24.04 | partial | foreign services and host ingress untouched across provision and destroy |
| `cleanup.md` | Ubuntu 24.04 | complete | all four cases proven live |
| `payload-boundary.md` | Ubuntu 24.04 | complete | both contract decisions settled and measured |

## Current gate

The local and contract suites are green, the AppArmor profile parses on Ubuntu,
and host prerequisites were installed without uninstalling or replacing foreign
web/database services. The root-owned installed helper predates the final
systemd collision fix and the nft counted-drop handling (the live kernel
correctly returned `EPERM` for a dropped UDP probe; the refreshed helper accepts
only `EPERM`/`EACCES` as that expected verdict and still verifies the counter).
Refreshing it requires a new interactive sudo consent;
the expired authorization is intentionally not bypassed. Until that refresh and
the nginx/Apache hostile, grant/revoke, exhaustion, warm-start, and cleanup runs
all pass, `ubuntu-nspawn` remains `implemented_unproven` and `adoptable=false`.

No failed or partial run is accepted as promotion evidence. Each live file must
include its durable job identifier, exact source identity, effective isolation
observations, host baseline comparison, cleanup result, and timing bounds.
The generated `sandbox.native-live-acceptance/v1` record carries these provenance
fields under `provenance`: `source.revision`, `source.worktree_clean`,
`source.harness_sha256`, and `durable_job_ids`. Transcribe or link that record
from the corresponding human-readable evidence file; neither a host date nor a
claim of a clean source tree is a substitute for the captured identity.
