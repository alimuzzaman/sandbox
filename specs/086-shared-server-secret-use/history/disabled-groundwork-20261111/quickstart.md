# Validation guide

Status: proposed checks for later implementation. No command below was executed by the planning shard. New flags are not implemented on baseline `e46777b`. A source check is not live-stack acceptance.

## Source groundwork prerequisites

Finish source implementation and matching docs through formal tasks/analyze gates first. Use disposable synthetic declarations containing names/references only, with an ordinary valid hosting declaration shape and exact required/generated mappings. Use an existing synthetic registered remote/project reference in tests; registration must not trigger connections. Do not read real configuration, owner environment, credential sources or metadata. Captured children use `tests.subprocess_support.run_test_process` with fixed synthetic overrides or `synthetic_environment`; never parent `os.environ`.

Have the authorized test helper run focused Python behavior checks and required repository gates later. It must report the actual command, pass/fail/skip counts, first failure excerpt and complete scratch log. This shard ran none.

## CLI scenarios after implementation

Illustrative synthetic references only; fixture paths are under the shared scratch root. Do not execute with real sources as part of source acceptance.

```text
./sb host validate --project-dir /Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/fixture --credential-scope server --remote fixture-server --credential-project fixture-project --credential-source fixture-source --credential-source-revision fixture-source-r1 --credential-profile fixture-profile --credential-profile-revision fixture-profile-r1 --json
```

Expect declarations valid, `server_credentials_static_validated`, availability unverified, execution disabled. The fixture supplies the ordinary hosting manifest required by the existing validator. This command must not resolve any provider value or server reference metadata. The implementation's accepted identifier grammar determines exact fixture spellings; use syntactically valid synthetic revisions, never a real supplied revision as a grant.

Repeat the same selection with `host secrets`. Expect unavailable inventory, no key absence claim or laptop fallback. With `host stack up --template <synthetic-template> --name fixture-stack --plan`, and separately `--confirm`, expect unsupported capability before remote setup/store/list/check, source export or any secret read. Direct host apply/stage/image/sync server-scope entry must also refuse. A malformed selection fails with fixed non-echoing diagnostics before these paths. No command can toggle enabled execution.

## Focused source behavior matrix

| Scenario | Observable assertion |
| --- | --- |
| Empty synthetic laptop credentials; server scope | Valid declaration succeeds, availability unverified; trap local/server secret-reader and process/network/write calls and assert zero calls |
| Server execution including stack plan and direct handler entry | Stable unsupported code before remote lookup/store, claims, target leases, staging/export/worktree, generation, jobs or provider clients |
| Missing source/profile/project/revision; mixed local/server flags; wrong server selector | Bounded invalid-selection result; no fallback, connection or key access. A well-formed but unverified server reference stays unverified, never authenticated |
| Client actor/grant/owner fields or inline provider values | Closed declaration rejects unauthorized fields or unsupported value payload without reflecting input in output |
| Exact mapping changes with unchanged destination set | Parsed immutable declarations differ for source-key, consumer, destination or source/profile revision changes; no claim of operational grant enforcement |
| Invented capability manifest/context/revision; repeated refusal | Always disabled, same fixed refusal, zero effects; no reservation/grant or durable admission minted |
| Server inventory | Unavailable without local/server metadata probes, generation or provisioning |
| Synthetic sentinel supplied in rejected input | Sentinel absent from stdout/stderr/result rendering; no files/artifacts/process launch. These are decoder leak checks, not provider execution leak proof |
| Missing provider plus `--generate` on server request | Unsupported before either generation or presence checking; never claim a real missing-key preflight occurred |
| Personal-local default versus explicit | Existing caller-visible behavior preserved without server references; existing generated-secret compatibility checks still apply |

Do not build fake ledger/grant machinery to claim replay tests passed. Repeated pure refusal is the only replay-like behavior executable in this slice; source request repetition must remain effect-free.

## Blocked installed integration acceptance

Do not run until proposed contract acceptance, retention of the reserved feature numbers 086 and sibling 085, installed capability/revision agreement and separate runtime authorization. Use [future composition](contracts/future-owner-broker.md) and [data model](data-model.md), not invented command syntax.

Required later cases include clean laptop/present declared server refs; missing and unauthorized refs; wrong server/install/actor/runtime revision; every source/profile/mapping/destination/consumer mutation; stale canonical target fence/source writer; two equal-rights actors including approved sudo; missing-key terminal no-effects refusal with `--generate`; repaired-source frozen replay; known-refusal linked new-id retry versus unknown original lookup; reservation/admission disconnects; concurrent identical and cross-actor requests; genuine retained committed admission before staging; cancellation/expiry before consume and between each effect; bounded provider rejection/consumer failure/timeout; no values in transport/argv/output/poll/log/artifact/cache/staged/retained storage; separately authorized generated credential delivery.

Record supported names/status inventory separately from provider validity. Do not treat synthetic actor/refusal fixtures, source hashes or context frames as installed proof. Full SC-003 through SC-008 and FR-023 acceptance remain blocked. Report source-groundwork result, test evidence and unavailable runtime proof as distinct outcomes.
