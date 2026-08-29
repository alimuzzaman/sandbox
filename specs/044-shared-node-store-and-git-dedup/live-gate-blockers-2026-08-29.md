# Spec 044 live-gate blockers — 2026-08-29

No Spec 044 remote job or named-store reclaim command was submitted in this lane.
The configured host was already under material pressure (96.93% memory used, about
367 MiB available, with active and queued jobs), so only index-independent gates were
allowed to proceed serially.

## T016

`collect_materialization_evidence()` creates and removes one local workspace and records
initial source integrity and a filesystem used-space observation. It does not run the
quickstart's reset, discard, dirty-layer unpack, test, and build sequence, or prove source
survival after every step on the remote host. Running its unit test remotely would not
satisfy T016, so no success claim or job ID was created.

## T017

`render_overlay_evidence()` proves generated overlay text and explicitly records
`package_scripts_run: false`. It does not create two real sibling workspaces, run
concurrent installs of different dependency versions, or exercise host-visible
preparation/removal as the ordinary operator. No disposable generic-Compose project with
those exact bounded commands was available, so T017 remains unverified.

## T018

T018 apply is destructive even when scoped to a disposable volume. There was no validated
disposable family from T017 and no exact independently reviewed human confirmation for a
plan ID and volume identity. Neither the read-only plan nor apply was run, preventing an
unreviewable plan from being mistaken for deletion authority.
