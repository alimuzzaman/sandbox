# Deployment-attempt metadata and bounded log evidence

Scope: the supplied `summary.md` and six named logs under
`/Users/alim/Sites/git/sandbox/tmp/deployment-review-attempts-20260908/`. This index
contains file metadata, exact delegated command names, terminal error/status text,
reported revisions, and reached phases. It omits incidental operands, secrets,
configuration values, paths from command output, and raw log dumps. These failed attempts
are evidence about preflight gates, not runtime, deployment, health, or production proof.

## Artifact inventory

Times are UTC filesystem mtimes. The logs have no embedded timestamps; the summary reports
that execution occurred approximately 2026-09-08 17:14–17:17 UTC.

| file | bytes | mtime | file_sha256 |
|---|---:|---|---|
| `summary.md` | 2114 | 2026-09-08T17:16:59.849372Z | `sha256:8750540ab70d966425719999960279ac9d2bc6bfe74087dec156b751af031a79` |
| `development.log` | 72 | 2026-09-08T17:14:15.606581Z | `sha256:c4dd9d50b9f705ba0a509aec172cab2317afbee15a59706b3287a7b471874b3a` |
| `production.log` | 72 | 2026-09-08T17:14:33.356784Z | `sha256:c4dd9d50b9f705ba0a509aec172cab2317afbee15a59706b3287a7b471874b3a` |
| `development-script.log` | 346 | 2026-09-08T17:14:55.317504Z | `sha256:50fde9b1a1387d85cdee7c37622f95c81bffa8c4b6e72f28eed14e79d8f9f25c` |
| `production-script.log` | 346 | 2026-09-08T17:15:05.934272Z | `sha256:50fde9b1a1387d85cdee7c37622f95c81bffa8c4b6e72f28eed14e79d8f9f25c` |
| `development-pinned-node.log` | 331 | 2026-09-08T17:15:39.833452Z | `sha256:a46305ca14ccf085447361c893e0b86d40e442d5d7664baee28df21de7cd0c35` |
| `production-pinned-node.log` | 332 | 2026-09-08T17:15:57.456748Z | `sha256:0f36eb330cb67717f4d0d110934a07f822f03f2ce57b7ce7777637970ba0ac9b` |

## Direct log proof by attempt

The exact commands below are the six commands named in the supplied summary. Exit code
`1` is explicit in the two pinned-Node logs. The first four logs show the terminal error
codes below but do not contain an explicit exit-code line; the summary separately reports
exit `1` for all six.

| exact command | log | direct terminal/error marker | phase reached | direct exit |
|---|---|---|---|---:|
| `pnpm deploy dev` | `development.log` | `ERR_PNPM_NOTHING_TO_DEPLOY` (`No project was selected for deployment`) | pnpm built-in deploy resolution; package script/image selection/activation not reached | unknown in log |
| `pnpm deploy prod` | `production.log` | `ERR_PNPM_NOTHING_TO_DEPLOY` (`No project was selected for deployment`) | pnpm built-in deploy resolution; package script/image selection/activation not reached | unknown in log |
| `pnpm run deploy dev` | `development-script.log` | `ERR_PNPM_UNSUPPORTED_ENGINE`; expected Node `24.18.0`, observed Node `v26.5.0` | engine gate before package script's Node wrapper; image selection/activation not reached | unknown in log |
| `pnpm run deploy prod` | `production-script.log` | `ERR_PNPM_UNSUPPORTED_ENGINE`; expected Node `24.18.0`, observed Node `v26.5.0` | engine gate before package script's Node wrapper; image selection/activation not reached | unknown in log |
| `bash scripts/run-with-repository-node.sh pnpm run deploy dev` | `development-pinned-node.log` | Node `v24.18.0` selected; `ELIFECYCLE` command failed with exit code `1`; Sandbox runtime must match clean local revision | application deployment script reached Sandbox runtime-revision gate; image selection/activation not reached | 1 |
| `bash scripts/run-with-repository-node.sh pnpm run deploy prod` | `production-pinned-node.log` | Node `v24.18.0` selected; `ELIFECYCLE` command failed with exit code `1`; Sandbox runtime must match clean local revision | application deployment script reached Sandbox runtime-revision gate; image selection/activation not reached | 1 |

The pinned logs report npm `v11.16.0` alongside Node `v24.18.0`. Their inner script lines
identify the dev/prod selector, but no revision hash is printed in either log.

## Assertions recorded only by the supplied summary

The following are explicitly attributed to `summary.md`; they are not independently
proven by the six log bodies:

- Lenzora control checkout clean commit: `9021d63f7e93357230321a037a044023dc35664d`.
- Sandbox checkout clean commit: `ef5a239dd1f4e443f63ecc546f373d70a50c6adc`.
- Eight unfinished schema-verifier files were preserved and restored with all eight
  SHA-256 hashes matching, and none was used by these attempts.
- All six commands exited `1` before image selection or activation; the attempts made no
  new build, deployment job, controller update, or production-data change.
- The last independent controller read before these attempts reportedly had local runtime
  revision `b1c44aad3fde1cf9f42d05a6` and installed revision
  `e0949074e80c469bcff36c12`; the pinned logs confirm only the existence of the mismatch
  refusal, not these hashes.

## Evidence boundaries and consistency check

- Direct evidence supports a deterministic progression: built-in pnpm deploy resolution
  failed first; script invocation then hit the Node engine mismatch; pinned Node reached
  the Sandbox runtime-revision guard and exited `1`.
- The summary's all-six exit-1 statement is stronger than the first four log bodies' own
  terminal evidence. It is retained as a parent assertion with that limitation.
- No named log reaches image selection, activation, health/readiness, edge, public route,
  or production state. No named log proves source cleanliness or absence of mutations;
  those remain summary assertions.
- The attempts do not establish that any deployment failed after runtime effects. They
  establish only pre-effect command/preflight refusals.
