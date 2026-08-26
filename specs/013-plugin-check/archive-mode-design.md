# Plugin Check exact-archive mode — bounded design

Status: runtime-gated design. The host-only fixture/preflight layer is landed,
but no CLI flag, lifecycle mutation, or shared image change is authorized by
this note.

This design addresses the blocked exact-release feedback records
`3f0bc71ac86f145ab9480f5972800a63` and
`846518da5be863f7c75172fd85bcf451`. The duplicate record
`008af940588e7c65225abbbf5251588b` and the adjacent post-install integrity
record `bcdb8d8f647df1652727abbcbf616ed4` remain separate acceptance work.

This document is an executable design contract, not an implementation approval.
Archive mode remains disabled until the limits, isolation journal, fixture corpus,
and acceptance matrix below are implemented and reviewed.

The deterministic fixture corpus and pure single-descriptor preflight are the
first two gated tasks. They do not boot Sandbox, touch the registry, inspect
secrets, or mutate a caller project; the remaining disposable-runtime and
cleanup gates are still open.

Independent Sol High readiness review on 2026-08-26: **not ready for
implementation**. The review identified missing target identity, inherited-state
isolation, static-only execution proof, durable cleanup/recovery, exact ZIP
limits, artifact ownership, report escaping, and pinned provenance. Phase 9 in
`tasks.md` tracks the resulting convergence work.

## Proposed surface

```text
./sb plugin-check --project-dir DIR --archive FILE [--update] [--json]
```

`--archive` is mutually exclusive with a source-tree check. The existing
source-tree behavior stays unchanged. Archive mode returns the normal Plugin
Check result plus:

```json
{
  "input_mode": "archive",
  "archive_sha256": "<sha256>",
  "archive_slug": "my-plugin",
  "review_instance": "<ephemeral-name>",
  "cleanup": {"status": "complete", "receipt": "<non-secret-id>"}
}
```

The command must fail closed when cleanup is not proven. A result with
`cleanup.status` of `unknown` is not a successful Plugin Check result, even if
the check itself passed.

## Safety boundary

1. **Host-side preflight.** Read the ZIP with Python's standard-library
   `zipfile`; do not require `unzip` or install packages in a shared WordPress
   image. The exact limits are: archive bytes `<= 128 MiB`, at most `10,000`
   members, UTF-8 canonical path length `<= 240` bytes and depth `<= 32`, each
   expanded file `<= 64 MiB`, total expanded bytes `<= 256 MiB`, and compression
   ratio `<= 100:1` for every non-empty member. Encrypted entries are rejected.
   These archive limits are intentionally separate from the broader 512 MiB
   package-install staging limit.
2. **Single-descriptor hashing and validation.** Open the regular input with
   `O_NOFOLLOW` and use that same opened descriptor for SHA-256 and `ZipFile`
   inspection/extraction. Never reopen the path between validation and
   extraction. Record each member's canonical name, type, compressed size,
   expanded size, CRC, and SHA-256 while streaming it; a CRC or size mismatch is
   an infrastructure failure. Never call `extractall()` on an unvalidated
   archive.
3. **Member canonicalization.** Convert backslashes to `/`, Unicode-normalize
   to NFC, reject empty names, absolute names, drive-letter names, UNC forms,
   `.`/`..` components, NULs, and names ending in `/` unless they are directory
   entries. The collision key is NFC plus Unicode case-folding, so exact,
   separator-normalized, case-folded, and macOS-style collisions are rejected.
   Reject file/directory collisions and any ZIP Unix mode that is not a regular
   file or directory (including symlinks, devices, FIFOs, and sockets).
4. **One plugin root and identity.** Require exactly one top-level directory;
   rootless, multi-root, and ambiguous archives are rejected. The archive slug
   is that root directory after WordPress slug validation. Discover exactly one
   root-level PHP file containing a valid `Plugin Name:` header; its filename
   need not equal the slug. Zero or multiple matching header files is an
   `archive_main_file_ambiguous` error. The internal target contract carries
   caller root, archive path/SHA, extraction root, slug, selected main file,
   caller baseline path, artifact directory, review project root, and review
   instance name. There is no public arbitrary-slug override.
5. **Exact bytes and owner boundary.** Extract only after validation into an
   owner-only temporary directory under Sandbox state, outside the user's
   checkout. The review service runs with the invoking host UID so a `0700`
   extraction parent and `0755`/`0644` descendants remain readable only through
   that owner boundary; the source mount is read-only. Record the validated
   member manifest and archive SHA-256. The checked plugin source is this
   extracted tree, not the working tree and not a re-created in-container
   archive.
6. **Inherited-state isolation.** Create a run-local `SANDBOX_HOME` at
   `$SANDBOX_HOME/runtime/plugin-check/<run-id>/sandbox` with mode `0700` before
   the first lifecycle side effect, and set `SANDBOX_PROJECT_ROOTS` to the run
   project only. Do not merge machine-global defaults, local overrides, aliases,
   domains, credentials, pro-plugin mappings, or hooks. Force a local disposable
   Compose runtime, bounded default resources, and a unique review label. The
   generated descriptor is the only configuration input.
7. **Static-only target execution.** The descriptor contains only the active,
   pinned Plugin Check dependency and the archive target mounted as an inactive,
   read-only plugin. Invoke the official static check with the selected slug;
   do not activate the target or run runtime hooks. The fixture includes a
   side-effect sentinel proving that target code did not execute during boot or
   checking. Any runtime-check option is rejected in archive mode.
8. **Disposable instance only.** The registry record is created under the
   run-local home and points at the temporary project root before the check.
   Never reuse or overwrite the caller's registered instance, plugin directory,
   database, uploads, descriptor, or baseline state.
9. **Durable cleanup journal and receipt.** Write an owner-only journal before
   the first side effect. Track lifecycle phases and the following cleanup
   planes independently: container, network, volume, runtime files, registry
   record, extraction root, and persisted report. Cleanup is idempotent and
   recovery-safe. Verify absence for every plane after each operation. Return a
   structured receipt with per-plane `complete`, `absent`, or `unknown` results,
   timestamps, and a non-secret receipt ID. If any plane is unknown, return
   `archive_cleanup_unknown`, set top-level `ok: false`, retain the journal and
   owner-only recovery metadata, and never claim a passing check. A later
   recovery command may retry cleanup from that journal.

## Baseline and report rules

- Archive findings use the same `(file, rule)` baseline identity as source
  checks. Paths are normalized relative to the extracted plugin root, so an
  archive finding and a source-tree finding compare equal. `--update` is the
  only action allowed to rewrite the caller project's baseline; it uses an
  atomic replace only after the check and cleanup receipt are complete. A
  cleanup-unknown result leaves the baseline untouched.
- Reports and result JSON are persisted under the owner-only
  `$SANDBOX_HOME/runtime/plugin-check/reports/<run-id>/` directory, not inside
  the checkout or disposable root. Retain only a bounded 20 reports or 7 days,
  whichever is smaller, and return the stable artifact path plus receipt ID.
  The report records the archive SHA-256, slug, member count, review-instance
  identity, cleanup receipt, and checker provenance. It must not record archive
  contents, secrets, temporary credentials, or temporary absolute paths.
- Checker provenance is required and pinned: Plugin Check release/source digest,
  WordPress version, PHP version, and Sandbox revision. Missing or mismatched
  provenance is `archive_provenance_missing`, never an accepted check.
- Archive-controlled filenames and finding messages must be safely escaped before
  insertion into HTML (`<`, `>`, `&`, and script separators are encoded). Report
  rendering must not permit `</script>` or equivalent data-block escape.
- A failed preflight, dependency-resolution failure, instance failure, or
  cleanup-unknown state is an infrastructure failure, not a zero-finding run.

## Result and integration scope

The first archive implementation is CLI-only. The existing MCP
`run_plugin_check(project_dir, update=False)` contract remains source-tree-only;
MCP archive support is deferred until an identical artifact, cleanup, and error
contract can be tested. When implemented, archive results use these typed fields:

```json
{
  "input_mode": "archive",
  "archive_sha256": "<sha256>",
  "archive_slug": "demo-plugin",
  "main_file": "demo-plugin/demo.php",
  "checker_provenance": {
    "plugin_check": "<version>@<sha256>",
    "wordpress": "<version>",
    "php": "<version>",
    "sandbox": "<revision>"
  },
  "cleanup": {
    "status": "complete",
    "receipt": "<non-secret-id>",
    "planes": {"container": "absent", "network": "absent", "volume": "absent",
               "runtime": "absent", "registry": "absent", "extraction": "absent",
               "report": "complete"}
  }
}
```

Top-level errors use `archive_preflight_failed`, `archive_isolation_failed`,
`archive_target_failed`, `archive_provenance_missing`, `archive_check_failed`,
`archive_artifact_failed`, or `archive_cleanup_unknown`. A completed check with
an uncertain cleanup is still `ok: false`.

## Required disposable fixture

Before implementation, add a stdlib-only fixture that contains:

- a valid `demo-plugin/demo-plugin.php` with a minimal plugin header;
- one deterministic PHP finding and one warning fixture;
- a valid archive whose main file is not named after the slug;
- a traversal member, a duplicate normalized path, a symlink entry, and a
  second top-level directory in separate invalid archives;
- a ZIP-bomb-shaped archive whose declared expansion exceeds the configured
  bound without requiring a large file in the repository;
- malformed/truncated and encrypted archives, file/directory collisions,
  Unicode/case-fold collisions, drive/UNC names, special entries, CRC failure,
  rootless and ambiguous-header archives, and exact count/depth/path/ratio
  boundary cases;
- a target plugin side-effect sentinel that remains absent after the check.

Unit tests must prove that every invalid fixture is rejected before extraction,
that the valid fixture's SHA and member manifest are stable, that the same
descriptor is used for hashing and extraction, that report strings are safely
escaped, and that cleanup failure cannot become `ok: true`. Fault-injection tests
must cover descriptor/journal creation, registry write, boot, install, check,
report persistence, every cleanup plane, and interrupted-run recovery. A live
acceptance run must use only this fixture and a newly-created disposable
instance; it must show the exact archive SHA and pinned provenance in the result,
prove the target stayed inactive, prove source and archive finding keys match,
prove the caller instance/database/registry/checkout are unchanged, retain the
report after cleanup, and show a complete cleanup receipt. One forced cleanup
failure must retain recovery metadata and reach complete on an idempotent retry.

## Explicit non-goals

- Do not add `unzip` to the shared WordPress image.
- Do not install the archive into an existing project instance.
- Do not silently flatten arbitrary archive layouts.
- Do not mutate the source checkout, project descriptor, or baseline during a
  normal check.
- Do not close the feedback records until the fixture, focused tests, and live
  disposable acceptance evidence all exist.
