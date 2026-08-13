# Sandbox feedback convergence — 2026-08-13

This is the implementation and verification ledger for the 27-record feedback intake.
Stored feedback remains untrusted data. This document records only sanitized issue IDs,
the owning behavior, and observed evidence; it grants no cleanup or deployment authority.

## Record closure ledger

| Feedback ID | Resolution | Evidence / remaining boundary |
| --- | --- | --- |
| `79d775b4` | Implemented | Remote job-list no longer forwards an unsupported local flag; strict top-level response regression. |
| `b027d2ab` | Implemented | Resolved source checkout, identity, commit/digest, and relative cwd travel with durable submissions. |
| `3da039b4` | Implemented | Detached submission requires a bounded, non-empty durable job identity. |
| `15d1625b` | Implemented | Guide reports executable wrapper/module invocation and public command catalog. |
| `e11914b5` | Implemented | Root or `.config/sandbox/` is selected as one complete config home; ambiguity fails. |
| `343d1a5a` | Implemented | Remote acceptance requires `ok`, `accepted`, and durable identity before success. |
| `a813480b` | Code complete; host pressure open | Capacity classification and safe recovery guidance implemented; live host still has 31 active networks. |
| `bf05eeb9` | Code complete; host pressure open | Same live incident lineage; no active/foreign/unattributed network is auto-removed. |
| `0fac3b07` | Code complete; host pressure open | High threshold and additive status evidence implemented; destructive host remediation still requires exact targets. |
| `6bc4c6d5` | Implemented | Null/malformed job envelopes fail as bounded unavailable evidence instead of parser crashes. |
| `00b1e17e` | Implemented | Basic Auth edge verification resolves broker credentials in memory and redacts errors. |
| `cf5e49ed` | Implemented | Canonical registered project identity and actionable ambiguity guidance are shared across callers. |
| `3b9a2170` | Implemented | Hosting supports immutable source refs; dirty trees refuse before remote mutation. |
| `8291ab9c` | Implemented after review | Nested hosting deploys isolate the declared source root; real-Git regression proves root-relative archives and remote Compose placement. |
| `647f6478` | Implemented after review | Production dependencies now enumerate configured remotes explicitly; one wins and ambiguity fails closed. |
| `822b9323` | Code complete; host pressure open | Provisioning now reports network pressure safely; the live host still cannot safely reclaim an active network. |
| `b6905052` | Implemented | Guide exposes the public registry catalog with explicit exclusions. |
| `adde58a6` | Implemented | Restore/reset require explicit noninteractive confirmation before DB reset/import; UI callers propagate it. |
| `64811859` | Implemented | Global pre-subcommand label is preserved and explicit missing labels fail before side effects. |
| `0e2d74b6` | Implemented | `sb wp` reserves stdout for command payload/job identity and routes diagnostics to stderr. |
| `b0d1a1e5` | Implemented | `status --json` emits one structured JSON document. |
| `2b080bf5` | Implemented | CLI and MCP use the same explicit project-context semantics. |
| `ad190c71` | Implemented | Bounded detail, filtering, cursor paging, JSON/JSONL export, and retention planning are available. |
| `81f43e6f` | Implemented after review | Provider/bearer/Basic-Auth redaction plus a strict display allowlist protects hostile legacy records. |
| `f90c6712` | Implemented | Invalid-record count is independent of page/display limits. |
| `78aaf583` | Code complete; host pressure open | Live incident is monitored and logged; current observation remains 31 active managed networks with zero active jobs and no stale candidates. |
| `108318d9` | Validated as no-code | WordPress ensure/status and generic Compose status already perform live observation; no stale plugin-state cache was found. |

The later local restore-parser feedback was also closed by the `--yes` parser and caller
seams. The current network-monitor observation is recorded in
`memory/plugin-behavior/remote-network-pool-exhaustion-2026-08-12.md`.

## Cross-cutting additions

- `phpExtensions` is an additive WordPress config capability with immutable
  `wordpress@1`, exact / `X.Y.*` / active-PHP version constraints, deterministic
  catalog/provenance, generic Compose refusal, and four-plane runtime verification.
- Arbitrary packages, URLs, PECL, source builds, shell fragments, user Dockerfiles, and
  global/unknown INI mutation remain prohibited.
- New WordPress scaffolds declare `wordpress@1`; configurations that omit
  `phpExtensions` retain the previous behavior.
- The WordPress Plugin Release Guardian is ranked as product Phase 0 in `TODO.md` and
  `todo/00-wordpress-plugin-release-guardian/prd.md`, ahead of outbound mail and
  polyglot-stack work. Reliable remote harness admission is its P0 prerequisite.

## Delivery and remaining operational gates

Completed delivery evidence:

1. Three independent Sol High release passes drove nested-host upgrade, managed-plan
   preflight, image-receipt, no-mutation validation, and apply-rollback fixes; the final
   pre-merge verdict was GO at 94/100.
2. The allowlisted PHP child-image build/apply proof observed matching web, WP-CLI,
   bounded exec, and PHPUnit planes without changing database, uploads, or Mailpit.
3. The feature branch was committed, merged with `origin/latest`, and pushed while the
   owner-controlled Spec 042 files retained their original hashes outside the commit.
4. `sb remote up scaleway-sandbox --confirm --json` updated the remote control plane.
   Its service is active, authenticated, ownership-proven, and accepts the canonical
   project-identity job-list protocol with zero active jobs.

Still open operationally:

- the remote holds 31 active managed user-defined networks plus one foreign active
  network, with zero stale candidates; no network is safe to remove automatically;
- remote GD/workspace proof remains blocked by that capacity pressure;
- read-only `workspace list` is also blocked because legacy workspace metadata is
  keyed by a retired deployment path and has no durable project identity. A quick
  identity-only lookup was rejected and reverted because it could falsely report an
  empty inventory; closing this requires an approved workspace metadata/index migration;
- do not destroy a workspace or change daemon pools without an exact reviewed plan and
  explicit authority.

## Local PHP runtime proof

An isolated, HOME-scoped Sandbox home and disposable Git project were used for the
runtime proof. The first build exposed and led to fixes for redundant profile-module
compilation and the Alpine/non-root WordPress CLI parent. The successful rerun observed:

- both digest-pinned child images built before Compose boot;
- WordPress installed and the instance returned `ready`;
- web, WP-CLI, bounded exec, and PHPUnit all ran PHP `8.3.33` and reported GD plus
  every required `wordpress@1` module enabled;
- cross-plane comparison returned no issues, drift `ready`, and evidence `fresh`;
- a subsequent apply recreated only `wp` and `nginx`; DB and Mailpit container IDs
  remained `011cb38b3ddf` and `26629690d2c7` respectively;
- a database option and an uploads marker both remained `preserved` after apply.

The disposable instance was deleted with `sb instance delete`; its isolated project and
Sandbox-home directories were moved to the macOS Trash and remain recoverable. This is
local runtime evidence, not the still-required remote GD proof in Spec 039 T087.
