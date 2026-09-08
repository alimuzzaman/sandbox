# Amar Sonar deployment evidence

Scope: only the supplied sanitized files under `/tmp/asb-editor-validation/` were
read. This record keeps file-backed evidence separate from the owner-task assertions.
It omits cookies, login tokens, config/env values, raw logs, raw browser payloads,
and auth-bearing URLs.

## Artifact metadata

Times are UTC filesystem mtimes. The original three files contain no common structured event
timestamp; the mtimes are retention metadata, not proof of when an operation began.

| file | bytes | mtime | file_sha256 |
|---|---:|---|---|
| `blocks-production-apply.stderr` | 65 | 2026-09-08T16:43:40.483092Z | `sha256:6bde1a0bbaf49c2417f10b487c23ab8df2df1895e360c9f85c462bc17a36c5b1` |
| `blocks-production-diagnose.json` | 3022 | 2026-09-08T16:44:15.290621Z | `sha256:43b748237833801a9fcfe36289fdf4fb4a671cb539cbb6e88afbc39c2cef1d36` |
| `production-blocks-browser.log` | 309 | 2026-09-08T16:44:26.985761Z | `sha256:095d645ea5daad634b84432de8fed44e9217a6469a3cc64b6bfae697e5bd850b` |

## File-backed initializer proof

Two additional sanitized captures under `/tmp/asb-editor-validation/` report the
initializer hash inputs and the resolved-hash comparison. Both outer JSON records
have `name = scaleway-sandbox`, `ok = true`, `exit_code = 0`, and empty stderr.

| file | bytes | mtime | file_sha256 |
|---|---:|---|---|
| `all-initializer-proof.json` | 2469 | 2026-09-08T17:24:28.559855Z | `sha256:eee115218d6014b5cb4c8c9dbd5fad6f157df741c9c210b50d8f5ba01c34f1e0` |
| `all-initializer-resolved-hashes.json` | 618 | 2026-09-08T17:25:45.106416Z | `sha256:c5e83e0acc920e3602f41c28531b6990e4d5cce07235f66605aff7420017f7e6` |

The captures contain six initializer names, six status records, and six zero exit
codes. Every status record uses project `sandbox-host-amarsonar-bangla-production`,
its service name matches the initializer name, and the six UTC timestamps are valid
and strictly increasing from `wp-permissions` through `frontend-editor-migration`.
The table records only safe identity and hash fields; container names and other
runtime payload are omitted.

| initializer | proof_hash | resolved_hash | status_label_hash | proof_label_match | resolved_label_match | timestamp_utc | image_digest | status |
|---|---|---|---|---:|---:|---|---|---|
| `wp-permissions` | `6f92561d5b075550970a41724b09d58ca8b8b7b6cf4684d48cc40b9700197d06` | `6f92561d5b075550970a41724b09d58ca8b8b7b6cf4684d48cc40b9700197d06` | `6f92561d5b075550970a41724b09d58ca8b8b7b6cf4684d48cc40b9700197d06` | true | true | 2026-09-08T16:41:57.822832485Z | `sha256:eea0813c7c9ddb73375e1ff57096038244688cfabd796240982ee3aec6cf5c7d` | `exited 0` |
| `wp-init` | `98daab4f14f4a39c23b056f730d43328cd5aba155408ad6f72c59c5ef3b3a242` | `98daab4f14f4a39c23b056f730d43328cd5aba155408ad6f72c59c5ef3b3a242` | `98daab4f14f4a39c23b056f730d43328cd5aba155408ad6f72c59c5ef3b3a242` | true | true | 2026-09-08T16:41:58.157258494Z | `sha256:eea0813c7c9ddb73375e1ff57096038244688cfabd796240982ee3aec6cf5c7d` | `exited 0` |
| `db-backup-before-host-migration` | `3e7e957dc8348465d92fe7210835f1076d4b9e1b2a1d83f614239b6e4a907dec` | `3e7e957dc8348465d92fe7210835f1076d4b9e1b2a1d83f614239b6e4a907dec` | `3e7e957dc8348465d92fe7210835f1076d4b9e1b2a1d83f614239b6e4a907dec` | true | true | 2026-09-08T16:41:58.429447659Z | `sha256:67873d30a17f6a9c331f06363b2fa15f38abca415529966d67c84f87f82439fe` | `exited 0` |
| `catalog-host-migration` | `646831d1bb1db7293a94d50b13c04f87d1f014a4a3a0bb33d99706d979743017` | `646831d1bb1db7293a94d50b13c04f87d1f014a4a3a0bb33d99706d979743017` | `646831d1bb1db7293a94d50b13c04f87d1f014a4a3a0bb33d99706d979743017` | true | true | 2026-09-08T16:41:58.780257750Z | `sha256:eea0813c7c9ddb73375e1ff57096038244688cfabd796240982ee3aec6cf5c7d` | `exited 0` |
| `db-backup-before-frontend-editor` | `2b53dae8056035954c3c1eaa81c32675b794dccdad353cc009435840eb0c1909` | `2b53dae8056035954c3c1eaa81c32675b794dccdad353cc009435840eb0c1909` | `2b53dae8056035954c3c1eaa81c32675b794dccdad353cc009435840eb0c1909` | true | true | 2026-09-08T16:41:59.100351175Z | `sha256:67873d30a17f6a9c331f06363b2fa15f38abca415529966d67c84f87f82439fe` | `exited 0` |
| `frontend-editor-migration` | `028265d67a1a69ca1285e3b20173b58db41a2d482fe568bb3d2930255eb272d2` | `7447eff722f654cde87663f46b0ec2afaa6623fdaf4c7d5a06865a0e4a9ddc09` | `7447eff722f654cde87663f46b0ec2afaa6623fdaf4c7d5a06865a0e4a9ddc09` | false | true | 2026-09-08T16:41:59.502809813Z | `sha256:eea0813c7c9ddb73375e1ff57096038244688cfabd796240982ee3aec6cf5c7d` | `exited 0` |

The original proof therefore has six records but only five proof-hash/label
matches. The resolved-hash capture has six of six label matches, including the
frontend-editor migration. This is initializer identity evidence only; it does not
show that production activation completed.

## File-backed final apply and status

`blocks-production-final-apply.json` reports `ok = true` for project
`amarsonar-bangla`, environment `production`, and explicitly selected remote
`scaleway-sandbox`. Its application commit is
`30b2ec2a9a3be65ed2fea95a217934842de3c3c1`; the derived source-revision check was
resolved at apply time with provider `pushed_commit_sha`.

| file | bytes | mtime | file_sha256 |
|---|---:|---|---|
| `blocks-production-final-apply.json` | 393 | 2026-09-08T17:28:37.108068Z | `sha256:c5940398e84caee2bc82454b53a560354f08db5127c15563465d8e0107ffbcc8` |
| `blocks-production-final-status.json` | 5035 | 2026-09-08T17:29:08.279287Z | `sha256:0897cafbaad992cdc24b4d168515a9e5a6bf9b167e220a57a8995dde45e86c0d` |
| `blocks-production-final-log.json` | 12600 | 2026-09-08T17:29:13.688571Z | `sha256:68b38019a3b81e6a587e031143b27675cd33b214cb03f60cc377a46279fa8fd9` |
| `production-blocks-final-browser.log` | 309 | 2026-09-08T17:29:38.201441Z | `sha256:095d645ea5daad634b84432de8fed44e9217a6469a3cc64b6bfae697e5bd850b` |

`blocks-production-final-status.json` reports `ok = true`, `state_record = present`,
generation `10`, and requested, staged, recorded, deployed, and observed runtime
revisions all equal to `30b2ec2a9a3be65ed2fea95a217934842de3c3c1`. Runtime, health,
and edge are each `ready`; edge-cache purge and latest recovery are null. Five of
five services are `running` and `healthy`. Declared, Compose, and running topology
counts are each five, with zero missing from Compose or runtime. The source-revision
projection is `ready`, with five of five `ASB_SOURCE_REVISION` checks matching.

The eight recorded phases are all `complete` and none is truncated; aggregate phase
bytes are 40,502. These final apply/status files establish a successful
generation-10 reconciliation of the existing runtime. They do not constitute a new
initializer execution proof; the six initializer records above remain the separate
initializer evidence. The final log has service lifecycle markers, but those markers
do not prove that any initializer was rerun or provide a replacement for the six
hash-checked records.

`blocks-production-final-log.json` is `ok = true` for the same project and
environment. No occurrence of the exact bundle digest supplied by the owner appears
in that log. The final browser marker file repeats six supported blocks and reports
`bundle_matches_release = true`, `editor_status = 200`, `canvas_loaded = true`,
`bootstrap = true`, `publication_available = true`, `review_available = true`,
`page_errors = 0`, and terminal marker `PRODUCTION_EDITOR_AND_PUBLIC_INDEX_PASS`.
It also contains no exact bundle digest. The exact digest
`sha256:c04ce533b320f482591b85bbbb98444ae6a827f34c19eaa9f283fba53c0ff1ed` is
therefore retained below as an owner-task assertion, not file-backed digest
evidence. The installed Sandbox runtime revision `bebe6ee03db17fb10c4c1b73` is
also an owner-task assertion.

## File-backed apply refusal

`blocks-production-apply.stderr` contains one terminal refusal marker:

- `error: initializer wp-init has foreign evidence; refusing replay`

No numeric exit code or structured request ID appears in this stderr file. It is direct
refusal text from the attempted apply. It does not show a runtime effect, deployment,
health transition, or production data change.

## File-backed production diagnosis

`blocks-production-diagnose.json` reports `$.ok = true` and `$.state_record = present`.
The allowlisted revision/generation fields are:

- `$.requested_revision = 30b2ec2a9a3be65ed2fea95a217934842de3c3c1`.
- `$.staged_revision = 30b2ec2a9a3be65ed2fea95a217934842de3c3c1`.
- `$.recorded_revision = 4802dad20ab3852a7fe4e857186bf556b79dfa6a`.
- `$.deployed_revision = 4802dad20ab3852a7fe4e857186bf556b79dfa6a`.
- `$.observed_runtime_revision = null`.
- `$.generation = 9`.

The source revision projection is `$.source_revision.state = degraded`. It has five
checks; all five have `state = mismatch` for the `ASB_SOURCE_REVISION` key using the
`pushed_commit_sha` provider, across five services. The service names are retained only
as a count here; their check values and incidental config are omitted.

The runtime and service observations are:

- `$.runtime.state = pending`, `$.health.state = ready`, `$.edge.state = pending`.
- Five services are `running` and `healthy`.
- Topology counts are five declared, five Compose, and five running; missing-from-Compose
  and missing-from-runtime counts are both zero.
- `$.image_state.state = unknown`; `$.images` is empty.
- `$.edge_cache_purge = null` and `$.latest_recovery = null`.
- Eight recorded phases (`epoch_start`, `compose_config`, `source_git`, `compose_runtime`,
  `compose_images`, `config_digests`, `source_revisions`, `epoch_end`) are each
  `state = complete` and `truncated = false`. Aggregate phase bytes are 36,538.

This diagnosis is a file-backed snapshot with healthy service counters but pending
runtime/edge state and a degraded source-revision projection. It is not itself a proof
that production activation completed.

## File-backed browser markers

`production-blocks-browser.log` has three lines of safe marker data:

- The supported-block marker contains six supported block entries and
  `bundle_matches_release = true`.
- Browser markers report `editor_status = 200`, `canvas_loaded = true`, `bootstrap = true`,
  `publication_available = true`, `review_available = true`, and `page_errors = 0`.
- Terminal marker: `PRODUCTION_EDITOR_AND_PUBLIC_INDEX_PASS`.

No bundle digest is present in this browser log or the supplied diagnosis file. The
bundle-match boolean therefore cannot be upgraded here to an exact digest comparison.
The browser lines do not contain a cookie, login token, or auth-bearing URL in this
bounded record.

## Owner-task assertions kept separate

The source owner reports, outside the three supplied files:

- Format fix commit: `0942d14a2b79504f8bfd58f383ca29fd6d0c581d`.
- Requested cherry-pick: `5bc35a2` onto `fd7d650f8bfbb1760d931e2484cc01fa0badf546`.
- Runtime revision: `e0949074e80c469bcff36c12`.
- Runtime still refused `wp-init` as foreign.
- A later Compose 5.4.0 reproduction found a raw-config hash versus resolved-`env_file`
  difference, with a resolved JSON pipe producing the actual config-hash label.

These claims are not printed by the three files and remain owner-task assertions. In
particular, the diagnosis's recorded/deployed revision and the owner-reported runtime
revision are different evidence fields; no reconciliation is inferred here.

## Evidence boundary

The original three files establish an apply refusal, a diagnostic snapshot, and successful browser markers, with no completed deployment proof at that time. The later final apply/status files separately establish successful generation-10 reconciliation of existing runtime. Initializer identity records, browser marker output, exact digest assertions, and the final deployment observation remain distinct evidence categories; none proves an unobserved fresh initializer rerun.
