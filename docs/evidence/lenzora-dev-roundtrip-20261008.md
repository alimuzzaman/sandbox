# Lenzora dev host round-trip evidence — 2026-10-08

This report records five dev round-trips between `xcloud-london` and `scaleway-sandbox`, pinned to Lenzora revision `5cd3c0e4b`. The final dev host is `xcloud-london`. The run exercised the maintenance and server-migration path; it is evidence for this test run, not a production cutover approval.

## Result

All ten legs completed. Nine legs passed the migration script end to end. `R5-out` returned exit 255 during its final old-host database count query because the SSH connection to the old host closed. The destination had already been restored, started, and passed the public health/revision check. Follow-up checks found the old app/workers stopped, the new app healthy and writable, matching database counts, and a clean storage inventory. `R5-back` then completed successfully and returned dev to `xcloud-london`.

For every completed leg, the database counts matched:

| Table | Rows |
|---|---:|
| User | 3 |
| Project | 43 |
| Snapshot | 445 |
| SnapshotArtifact | 2,223 |
| ComparisonArtifactObject | 2,079 |
| Job | 315 |

Storage checks reported 1,894 snapshots, 1,825 comparisons, and 847 resources, with no missing files, size mismatches, or ambiguous references. `R5-out`'s script did not record its final checks because it exited at the old-host query; those values came from separate read-only follow-up checks. Do not count that script exit as a pass.

## Recorded leg durations

Timestamps are UTC. These are active leg durations; the pause between failed `R5-out` verification and starting `R5-back` is excluded.

| Leg | Direction | Duration | Script result |
|---|---|---:|---|
| R1-out | xcloud → scaleway | 11m 14s | pass |
| R1-back | scaleway → xcloud | 9m 01s | pass |
| R2-out | xcloud → scaleway | 13m 06s | pass |
| R2-back | scaleway → xcloud | 8m 58s | pass |
| R3-out | xcloud → scaleway | 12m 06s | pass |
| R3-back | scaleway → xcloud | 8m 22s | pass |
| R4-out | xcloud → scaleway | 12m 31s | pass |
| R4-back | scaleway → xcloud | 10m 36s | pass |
| R5-out | xcloud → scaleway | 16m 02s | failed final old-host query; destination independently verified |
| R5-back | scaleway → xcloud | 8m 11s | pass |

The sum of recorded leg durations is 1h 50m 07s. It excludes the 7m 43s between `R5-out` ending at 14:22:16Z and `R5-back` starting at 14:29:59Z, plus any time outside the recorded leg boundaries.

Within cutover, stopping old app services to starting the dump took 12–15s, the database dump 41–65s, the storage transfer 26–230s, restore 97–163s, and target start plus health verification 59–154s. Freeze-to-script-result took 247–509s. Storage transfer and the repeated deploy recovery step are the clearest time-variance targets in this run.

## Process exercised

Each leg re-armed the target as writable, deployed the pinned revision, enabled read-only mode on both hosts, and ran `tools/server-migration/cutover-compose-data.sh`. The cutover stopped the old app services, copied the final database dump and storage volume, restored and started the target, then checked health. The harness compared six database tables, checked referenced storage files, verified the public health revision, and removed transfer dump/tar files after success while keeping stopped-service lists for rollback.

Every leg's first deploy attempt returned `recovery_context_required`. The harness retired that failed delivery and retried; the second attempt succeeded on all ten legs. This added the same recovery step to every migration leg.

The run harness and unredacted operational outputs remain in local scratch because they include command and host details:

- `/Users/alim/Sites/git/.scratch/lenzora/read-only-mode/roundtrip-run.sh`
- `/Users/alim/Sites/git/.scratch/lenzora/read-only-mode/roundtrip-leg.sh`
- `/Users/alim/Sites/git/.scratch/lenzora/read-only-mode/roundtrip-timings.py`
- `/Users/alim/Sites/git/.scratch/lenzora/read-only-mode/check-storage-files.sh`
- `/Users/alim/Sites/git/.scratch/lenzora/read-only-mode/roundtrip/` — aggregate run log and per-leg deploy, retire, cutover, and storage outputs

The local `roundtrip/sha256-manifest.txt` records hashes for the preserved run artifacts.

## Process improvements to evaluate

1. Add a supported deploy preflight or recovery action for stale staged deliveries. All ten legs needed a first-attempt failure, retirement, and retry.
2. Make cutover stages durable and resumable. `R5-out` reached destination start and health verification, then exited 255 because the final old-host count query lost SSH. Report that as post-cutover verification failure and allow safe verification retry without repeating the copy.
3. Separate source-side verification and cleanup from destination readiness. The destination can be healthy and writable while the old-host evidence query is unavailable; preserve both states in the result.
4. Add a fault-injection check for loss of the old-host connection after restore/start. Acceptance should prove that rerunning final verification does not repeat destructive restore steps.

Separate operator note from the maintenance-feature rehearsal: the initial drain census included non-claimable queued work and did not reach idle promptly. Feature commit `aaa6827` changes the census to count only claimable work, but it was not part of the deployed revision used in these ten legs. Validate that fix on its own before treating drain readiness as proven by this round-trip.
