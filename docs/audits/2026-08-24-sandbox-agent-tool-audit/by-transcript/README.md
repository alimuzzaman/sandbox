# Findings by safe source reference

The aggregate [findings index](../findings.md) is organized by opportunity. This
directory preserves the same findings under deterministic safe references. The
reference is the first 20 hexadecimal characters of
`sha256("sandbox-agent-tool-audit:v1:" + raw_id)`, prefixed with `CODEX-SRC-`.
No reverse map is retained.

| Finding ID | Safe source | Evidence class |
|---|---|---|
| ATO-001 | [`CODEX-SRC-b0cd2f139137896fc41b`](CODEX-SRC-b0cd2f139137896fc41b.md) | remote storage/deep attribution rollout |
| ATO-002 | [`CODEX-SRC-d0c49010c51e6c34fd86`](CODEX-SRC-d0c49010c51e6c34fd86.md) | CI agent-use cross-check |
| ATO-003 | [`CODEX-SRC-b0cd2f139137896fc41b`](CODEX-SRC-b0cd2f139137896fc41b.md) | remote storage/deep attribution rollout |
| ATO-004 | [`CODEX-SRC-b0cd2f139137896fc41b`](CODEX-SRC-b0cd2f139137896fc41b.md) | remote storage/deep attribution rollout |
| ATO-005 | [`CODEX-SRC-b0cd2f139137896fc41b`](CODEX-SRC-b0cd2f139137896fc41b.md) | remote storage/deep attribution rollout |
| ATO-006 | [`CODEX-SRC-1fc24f65c9da2980e674`](CODEX-SRC-1fc24f65c9da2980e674.md) | remote-only targeting cross-check |
| ATO-007 | [`CODEX-SRC-b0cd2f139137896fc41b`](CODEX-SRC-b0cd2f139137896fc41b.md) | remote storage/deep attribution rollout |
| ATO-008 | [`CODEX-SRC-b0cd2f139137896fc41b`](CODEX-SRC-b0cd2f139137896fc41b.md) | remote storage/deep attribution rollout |
| ATO-009 | [`CODEX-SRC-b0cd2f139137896fc41b`](CODEX-SRC-b0cd2f139137896fc41b.md) | remote storage/deep attribution rollout |
| ATO-010 | [`CODEX-SRC-d0c49010c51e6c34fd86`](CODEX-SRC-d0c49010c51e6c34fd86.md) | Check CI runner status |
| ATO-011 | [`CODEX-SRC-b0cd2f139137896fc41b`](CODEX-SRC-b0cd2f139137896fc41b.md) | remote storage/deep attribution rollout / Luna source review |
| ATO-012 | [`CODEX-SRC-b0cd2f139137896fc41b`](CODEX-SRC-b0cd2f139137896fc41b.md) | remote storage/deep attribution rollout / Luna source review |
| ATO-013 | [`CODEX-SRC-b0cd2f139137896fc41b`](CODEX-SRC-b0cd2f139137896fc41b.md) | remote storage/deep attribution rollout / Luna source review |
| ATO-014 | [`CODEX-SRC-b0cd2f139137896fc41b`](CODEX-SRC-b0cd2f139137896fc41b.md) | remote storage/deep attribution rollout / Luna source review |
| ATO-015 | [`CODEX-SRC-b0cd2f139137896fc41b`](CODEX-SRC-b0cd2f139137896fc41b.md) | remote storage/deep attribution rollout / Luna source review |
| ATO-016 | [`CODEX-SRC-b0cd2f139137896fc41b`](CODEX-SRC-b0cd2f139137896fc41b.md) | remote storage/deep attribution rollout / Luna source review |
| ATO-017 | [`CODEX-SRC-409992bca83e0fee7c74`](CODEX-SRC-409992bca83e0fee7c74.md) | Hermes setup rollout |
| ATO-018 | [`CODEX-SRC-1ef32de148e66c485200`](CODEX-SRC-1ef32de148e66c485200.md) | CLI/MCP surface sweep |
| ATO-019 | [`CODEX-SRC-1ef32de148e66c485200`](CODEX-SRC-1ef32de148e66c485200.md) | CLI/MCP surface sweep |
| ATO-020 | [`CODEX-SRC-9d3983e2ec663eac3b54`](CODEX-SRC-9d3983e2ec663eac3b54.md) | retention gap sweep |
| ATO-021 | [`CODEX-SRC-1fc24f65c9da2980e674`](CODEX-SRC-1fc24f65c9da2980e674.md) | remote storage/operator transcript plus Luna source review |
| ATO-022 | [`CODEX-SRC-409992bca83e0fee7c74`](CODEX-SRC-409992bca83e0fee7c74.md) | Hermes setup rollout |
| ATO-023 | [`CODEX-SRC-409992bca83e0fee7c74`](CODEX-SRC-409992bca83e0fee7c74.md) | Hermes setup rollout |
| ATO-024 | [`CODEX-SRC-409992bca83e0fee7c74`](CODEX-SRC-409992bca83e0fee7c74.md) | Hermes setup rollout |
| ATO-025 | [`CODEX-SRC-409992bca83e0fee7c74`](CODEX-SRC-409992bca83e0fee7c74.md) | Hermes setup rollout |
| ATO-026 | [`CODEX-SRC-409992bca83e0fee7c74`](CODEX-SRC-409992bca83e0fee7c74.md) | Hermes dashboard/TUI resume evidence |
| ATO-027 | [`CODEX-SRC-6a9a7779c9d1442ce649`](CODEX-SRC-6a9a7779c9d1442ce649.md) | delegated validation rollout |

ATO-011 through ATO-016 are source-contract findings discovered while expanding
the first storage workflow with Luna Max. They remain attached to that
safe source because that workflow supplied the triggering resource, remote,
and workspace evidence. ATO-017 through ATO-027 are the follow-up Hermes,
CLI/MCP, retention, remote-resource, and delegated-validation expansion. The
source file/line references remain in the canonical findings document.
