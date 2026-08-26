# Plugin Check exact-archive mode — bounded design

Status: design only. No CLI flag, runtime mutation, or shared image change is
authorized by this note.

This design addresses the blocked exact-release feedback records
`3f0bc71ac86f145ab9480f5972800a63` and
`846518da5be863f7c75172fd85bcf451`. The duplicate record
`008af940588e7c65225abbbf5251588b` and the adjacent post-install integrity
record `bcdb8d8f647df1652727abbcbf616ed4` remain separate acceptance work.

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
   image. The input must be a regular, non-symlink file, within the same size
   limit used by host package staging, and hashed before extraction.
2. **Member validation.** Reject malformed archives, absolute names, `..`
   traversal (including backslash forms), duplicate names, normalization
   collisions, symlink/special-file entries, excessive member count, and an
   uncompressed-size/expansion limit. Never call `extractall()` on an
   unvalidated archive.
3. **One plugin root.** Require one top-level plugin directory and a valid
   WordPress plugin slug. Require a matching main PHP file with a readable
   plugin header. Reject multi-root or rootless archives instead of guessing
   which directory is the plugin.
4. **Exact bytes.** Extract only after validation into an owner-only temporary
   directory under Sandbox state, outside the user's checkout. Record the
   validated member manifest and archive SHA-256. The checked plugin source is
   this extracted tree, not the working tree and not a re-created in-container
   archive.
5. **Disposable instance only.** Generate a minimal temporary project
   descriptor whose self-plugin path is inside the temporary extraction root.
   Copy only explicitly allowlisted non-secret runtime settings (WordPress/PHP
   versions and safe plugin-check dependencies). Never reuse or overwrite the
   caller's registered instance, plugin directory, database, or baseline
   state. The generated instance name must be unique and the registry record
   must point at the temporary project root before any check runs.
6. **Cleanup receipt.** Stop/destroy the temporary instance, remove its
   registry record, and remove the extraction root in a `finally` path. Verify
   each plane afterward. If any plane is uncertain, return a typed cleanup
   failure and retain only an owner-only diagnostic directory for explicit
   operator cleanup; do not report success.

## Baseline and report rules

- Archive findings use the same `(file, rule)` baseline identity as source
  checks. `--update` is the only action allowed to rewrite the project's
  baseline, and the result must identify that the input was an archive.
- The report records the archive SHA-256, slug, member count, review-instance
  identity, and cleanup receipt. It must not record archive contents, secrets,
  or temporary credentials.
- A failed preflight, dependency-resolution failure, instance failure, or
  cleanup-unknown state is an infrastructure failure, not a zero-finding run.

## Required disposable fixture

Before implementation, add a stdlib-only fixture that contains:

- a valid `demo-plugin/demo-plugin.php` with a minimal plugin header;
- one deterministic PHP finding and one warning fixture;
- a traversal member, a duplicate normalized path, a symlink entry, and a
  second top-level directory in separate invalid archives;
- a ZIP-bomb-shaped archive whose declared expansion exceeds the configured
  bound without requiring a large file in the repository.

Unit tests must prove that every invalid fixture is rejected before extraction,
that the valid fixture's SHA and member manifest are stable, and that cleanup
failure cannot become `ok: true`. A live acceptance run must use only this
fixture and a newly-created disposable instance; it must show the exact archive
SHA in the result and a complete cleanup receipt.

## Explicit non-goals

- Do not add `unzip` to the shared WordPress image.
- Do not install the archive into an existing project instance.
- Do not silently flatten arbitrary archive layouts.
- Do not mutate the source checkout, project descriptor, or baseline during a
  normal check.
- Do not close the feedback records until the fixture, focused tests, and live
  disposable acceptance evidence all exist.
