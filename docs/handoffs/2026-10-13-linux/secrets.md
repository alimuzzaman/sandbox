# Secrets work: Linux handoff

The user requested publication of the remaining Sandbox files on 2026-10-13.
This topic branch preserves the four research reports that were previously
outside Git in Mac scratch. It also commits the existing `.specify/feature.json`
selection of `specs/066-remote-storage-reservation`, without changing that spec.

## What is published

- [Conversation handoff](../mac-to-linux-2026-10-13.md): latest discussion,
  implemented CLI versus proposed keyring commands, and backlog safety limits.
- [aws-vault and keyring](secrets-research/aws-vault.txt).
- [envchain and GitHub CLI](secrets-research/envchain-gh.txt).
- [pass and Docker credential helpers](secrets-research/pass-docker.txt).
- [SOPS, age, and age-plugin-se](secrets-research/sops-age.txt).

The four reports are preserved verbatim as historical research. All are
partial; their source references, inferences, and unresolved questions remain
visible. They are not a validated backend selection, current security verdict,
or authority to run example commands. No keyring backend or migration has been
implemented by this work. Follow the current
[secret-inspection procedure](../../../skills/secret-inspection/SKILL.md).

The previous handoff's statement that these reports were Mac-only is superseded
by this publication. Its source-state snapshot likewise predates the now-owned
feature-pointer commit. Original scratch copies can remain on the Mac, but Linux
no longer needs them to recover this research.

## Transfer limits

Secret files, credentials, keyrings, environment dumps, provider sessions, raw
private data, and machine-local feedback records are excluded. This publication
covers Sandbox only. It does not commit another project's interrupted work or
the private Upwork commits mentioned in the consolidated migration report.

The parent migration coordinator owns cross-project collection. Do not resend
requests to other projects or resume their implementation from this document.
No deployment, remote runtime installation, credential migration, or key
deletion is authorized here. Implementation remains paused until the user
resumes it. Tests for this preservation pass run remotely only.

## Verification for this publication

- On `alim-predator-ph315-51`, `python3 -m unittest
  tests.test_architecture_boundaries` passed all 24 tests in 19.513 seconds,
  exit code 0. The child used `tests.subprocess_support.run_test_process`
  with its synthetic environment and a 90-second deadline.
- The tested source was a Git archive of
  `084a622001d2364edcdd766879cab7c9d5be9342`, restricted to the complete
  `sandbox`, `tests`, `mcp`, and `tools` trees. This publication changes only
  documents/reports and the feature pointer, not those tested source trees.
- An initial full-archive extraction refused an unrelated legacy skill
  symlink before any test started. The extraction protection stayed enabled
  for the restricted source archive.
- All four published reports were byte-compared with the original scratch
  reports. The six relative links in this handoff resolved.

No secret-backend runtime, credential migration, or deployment acceptance was
run. Research remains partial despite the passing architecture suite.

## Continue on Linux

Fetch `codex/secrets-research-handoff-20261013` from `alimuzzaman/sandbox` and
read this file. Use a clean checkout or a separate worktree; preserve existing
Linux edits. No merge into a shared branch is needed to read the handoff.

```text
Read docs/handoffs/2026-10-13-linux/secrets.md on
codex/secrets-research-handoff-20261013. Restore the secrets/keyring discussion
and research context, then report readiness. Keep implementation and credential
migration paused until I resume them.
```
