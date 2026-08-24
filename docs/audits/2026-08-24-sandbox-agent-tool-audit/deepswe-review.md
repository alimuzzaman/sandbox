# DeepSWE review for the Sandbox agent/tool audit

**Reviewed:** 2026-08-24 (public sources only)
**Scope:** DeepSWE repository, v1.1 data/leaderboard pages, task pages, one
representative public run, `PROVENANCE.md`, and public audits/issues.
**Non-goals:** no credentials, no private access, no bulk benchmark download,
no ingestion or redistribution of benchmark data, and no Sandbox product-code
changes.

## Executive assessment

DeepSWE is a useful reference for *how to package and verify an agent task*,
not a corpus that Sandbox should ingest. Its strongest transferable ideas are:

- pin the task's source revision and runtime contract;
- run a behavior-focused verifier in a separate pristine environment;
- retain a structured per-run receipt (patch, test report, raw log, trajectory);
- record model/harness/effort, steps, tokens, cost, duration, and outcome
  semantics together; and
- publish a deterministic subset recipe and an exclusions/error ledger.

The public release is not a uniform promise that every full transcript and
receipt is retrievable. The v1.1 site is primarily an outcome/metric browser;
the release manifest publishes object-key patterns, and a representative
GPT-5.6 Luna trial currently exposes an ATIF trajectory plus patch, agent-log,
and verifier-output objects. However, issue [#59](https://github.com/datacurve-ai/deep-swe/issues/59)
records model/config-specific `403 AccessDenied` responses even when trial
metadata said `has_trajectory`, `has_model_patch`, `has_agent_log`, and
`has_verifier_output`; issue [#52](https://github.com/datacurve-ai/deep-swe/issues/52)
and the independent [v1.1 audit](https://www.june.kim/auditing-deepswe-v1-1)
still describe boolean metadata and missing re-grade receipts in the public
artifact snapshot. On 2026-08-24, bounded `HEAD` checks for the representative
trial returned `200` for all four release-manifest paths, so #59 should be
treated as a historical availability finding, not current proof that those
objects are inaccessible.

The right Sandbox conclusion is therefore **adopt the evidence model and
verifier discipline; do not adopt the public corpus, hidden tests, or a binary
pass-only outcome model without privacy, licensing, and stateful-runtime
changes**.

## What was inspected

| Evidence | What it establishes |
| --- | --- |
| [DeepSWE repository](https://github.com/datacurve-ai/deep-swe) and [README](https://github.com/datacurve-ai/deep-swe/blob/main/README.md) | 113 original tasks across five languages; Harbor task layout; v1.1 separate verifier environment; canonical receipt names; Pier/mini-swe-agent execution. |
| [v1.1 data browser](https://deepswe.datacurve.ai/data/v1.1), [task catalog](https://deepswe.datacurve.ai/data/v1.1/tasks), and [trial table](https://deepswe.datacurve.ai/data/v1.1/trials) | Public outcome browser with pass rate, cost, total cost, steps, input/output tokens, peak context, duration, trial count, and error views. Pages are client-rendered and the trial index is not a transcript archive. |
| [Current homepage/leaderboard](https://deepswe.datacurve.ai/) and [`leaderboard-live.json`](https://deepswe.datacurve.ai/artifacts/v1.1/leaderboard-live.json) | Current model × effort rows, confidence intervals, and efficiency fields. The raw artifact records ledger values and an `as-of` generation timestamp; the UI may apply later pricing adjustments. |
| [Release manifest](https://deepswe.datacurve.ai/artifacts/v1.1/release.json) | Object-storage base URL and patterns for trajectory, model patch, agent log, and verifier output; no per-trial index, hashes, image digests, or retention guarantee. |
| [Representative task page](https://deepswe.datacurve.ai/data/tasks/abs-module-cache-flags), [task TOML](https://raw.githubusercontent.com/datacurve-ai/deep-swe/main/tasks/abs-module-cache-flags/task.toml), [instruction](https://raw.githubusercontent.com/datacurve-ai/deep-swe/main/tasks/abs-module-cache-flags/instruction.md) | Concrete prompt, pinned base commit, language, image tag, limits, no-network/separate verifier, and agent/verifier timeouts. |
| [Verifier entrypoint](https://raw.githubusercontent.com/datacurve-ai/deep-swe/main/tasks/abs-module-cache-flags/tests/test.sh), [grader](https://raw.githubusercontent.com/datacurve-ai/deep-swe/main/tasks/abs-module-cache-flags/tests/grader.py), [node-ID config](https://raw.githubusercontent.com/datacurve-ai/deep-swe/main/tasks/abs-module-cache-flags/tests/config.json) | Patch application, test reports, pass-to-pass/fail-to-pass node IDs, missing-test-as-failure semantics, binary/partial rewards, and CTRF provenance. |
| [Environment Dockerfile](https://raw.githubusercontent.com/datacurve-ai/deep-swe/main/tasks/abs-module-cache-flags/environment/Dockerfile) | Clone at the base commit, removes remote/future history/tags, garbage-collects, and installs pinned test tooling. |
| [Public test patch](https://raw.githubusercontent.com/datacurve-ai/deep-swe/main/tasks/abs-module-cache-flags/tests/test.patch) and [public solution patch](https://raw.githubusercontent.com/datacurve-ai/deep-swe/main/tasks/abs-module-cache-flags/solution/solution.patch) | Tests and reference solution are withheld from the agent during a run but are inspectable after the public task release. |
| [Representative ATIF trajectory](https://d3ujjcmjq6o8v6.cloudfront.net/v1.1/trial-artifacts/abs-module-cache-flags__AEwzdq5/agent/trajectory.json) | A currently readable full step transcript for `mini-swe-agent`/`openai/gpt-5.6-luna`, including messages, tool calls/results, timestamps, per-step metrics, and final totals. |
| [Apache license](https://raw.githubusercontent.com/datacurve-ai/deep-swe/main/LICENSE) and [PROVENANCE](https://raw.githubusercontent.com/datacurve-ai/deep-swe/main/PROVENANCE.md) | Apache-2.0 covers Datacurve's original task/harness/curation work only; each upstream repository keeps its own listed license. |

The public fetch of one v1 trial page timed out/was too large while task pages
were readable. That is a transport/UI limitation, not evidence that a trial is
missing. No bulk `trials.json` or gated dataset was downloaded.

## Task format and verifier isolation

The repository uses Harbor's compact task contract:

```text
task.toml         metadata, source commit, image, limits
instruction.md    agent-visible prompt
environment/      reproducible image fallback
tests/            verifier entrypoint, test patch, grader config
solution/         reference patch for review, not grading
```

The sampled `task.toml` has `schema_version = "1.3"`, a full upstream commit
SHA, language and repository URL, an image tag, `agent.timeout_sec = 5400`,
`verifier.timeout_sec = 1800`, and 2 CPU/8 GiB/20 GiB resource limits. Both
agent and verifier are declared `no-network`; v1.1 uses a **separate verifier
environment**. Pier's README also describes per-agent network allowlists for
cases where an agent needs narrowly scoped egress.

The verifier flow is unusually auditable:

1. The collector extracts the agent's committed diff as `model.patch`.
2. The verifier resets only files touched by that patch to the pinned base and
   applies the patch in a pristine container.
3. It applies the verifier's `test.patch`, runs the repository/new suites, and
   writes `reward.json`, `ctrf.json`, `test-stdout.txt`, `run.log`, and native
   reports under `verifier/`.
4. `config.json` supplies pass-to-pass (P2P) regression IDs and fail-to-pass
   (F2P) challenge IDs. A missing or skipped node is a failure; duplicate IDs
   resolve by worst status. Binary reward is 1 only when every F2P test passes
   and no P2P test fails; `f2p`, `p2p`, and `partial` fractions remain visible.

The task authoring method is also worth copying: run each verifier three times
to detect flake, include existing regression checks, and require LLM-assisted
analysis plus independent human review. The methodology says tests should
assert public behavior/observable output rather than private helper names, so
multiple internal implementations can pass.

There are important limits to this evidence. The public repo contains the
reference and test patches after release, so “held out” means held out from the
agent at execution time, not permanently secret. The Dockerfile uses an image
**tag**, not a content digest. `release.json` likewise has no image or artifact
hashes. A reproducible Sandbox release needs both.

## Transcripts, metadata, and per-run receipts

The distinction matters:

- **Outcome/aggregate metadata:** The v1.1 data browser and leaderboard expose
  task/model outcomes and aggregate fields. The raw leaderboard declares that
  `pass@1` is the scored attempt rate, `pass@4` is “at least one pass” over
  tasks attempted, context-window failures and agent timeouts are failures, and
  provider/verifier/network errors are excluded. This definition is stronger
  than a single unqualified `pass_rate` number.
- **Per-trial metadata:** Release data uses flags such as `has_trajectory` and
  `has_model_patch`, plus trial/model/config identifiers. A flag is not an
  artifact link. The v1.1 audit explicitly calls this out, and #52 recommends
  publishing receipts with a manifest.
- **Full trajectory:** The sampled CloudFront object is `ATIF-v1.7`. It contains
  the system/user/agent messages, serialized `reasoning_content`, timestamps,
  shell tool calls and arguments, observations/return codes, and per-step
  `prompt_tokens`, `completion_tokens`, `cached_tokens`, and reasoning/text
  token details. Its `final_metrics` contains `total_prompt_tokens`,
  `total_completion_tokens`, `total_cached_tokens`, `total_steps`, total
  reasoning/text tokens, and peak context. This is a full agent transcript for
  that run, not merely a trajectory length or outcome row.
- **Other receipts:** The release manifest names `model.patch`,
  `mini-swe-agent.txt`, and `verifier/test-stdout.txt`. Bounded HEAD requests
  on 2026-08-24 returned 200 for all four paths for the representative Luna
  trial. Availability has not historically been uniform (#59), and the public
  UI does not enumerate every object; do not infer “all transcripts published”
  from a `has_*` flag.

The gated [DeepSWE leaderboard dataset](https://huggingface.co/datasets/datacurve/deep-swe-leaderboard)
claims to store the full trajectories, logs, patches, and verifier output for
every official trial (32 GB, CC-BY-4.0, contact-sharing gate). The separate
[DeepSWE task dataset](https://huggingface.co/datasets/datacurve/deep-swe) is
explicitly held-out, gated, and does not show a comparable open license. We did
not access either gated dataset. Sandbox should treat third-party run content
as untrusted, potentially sensitive data and store only approved, redacted
receipts.

## Metrics and current reporting caveats

The raw leaderboard records useful per-configuration statistics: pass@1,
pass@4, attempted/passed counts, confidence interval and method, number of
whole-benchmark runs, mean/median USD cost, input/output tokens,
duration, agent steps, peak context, and output tokens on passing trials. For
example, the raw `gpt-5-6-luna[max]` row reports 301/448 scored attempts,
102/113 tasks passed at least once, 4 repeated passes, mean output 73,400
tokens, mean input 15.44M tokens, mean steps 101.7, and a pass@1 of 67.19%.
The artifact's raw mean cost is $3.03/task. The live homepage displays about
$0.61/task for the same Luna configuration after its later price adjustment;
[issue #68](https://github.com/datacurve-ai/deep-swe/issues/68) documents the
0.2× repricing calculation, and the [changelog](https://deepswe.datacurve.ai/changelog)
says the board was updated. This is a concrete reason to carry provider/route,
price schedule, and `as_of` metadata alongside cost rather than treating a
dashboard number as immutable.

Issue [#55](https://github.com/datacurve-ai/deep-swe/issues/55) also captures a
good reporting requirement: record exact model ID/snapshot and exact reasoning
effort, with pass rate, interval, cost, output tokens, and agent steps. Current
rows include model, harness, and effort, but not a dated provider snapshot or
route in the row itself. Do not compare “model” rows without accounting for
harness and effort.

## Reproducibility, QA, and known audit findings

**What is strong:** immutable base commits (where complete), deterministic
subset sampling (`--n-tasks 10 --sample-seed 0`), isolated images, bounded
resources, no-network verifier mode, a fixed mini-swe-agent harness for the
official board, behavior-level tests, and a documented receipt schema. The
Dockerfile's removal of remote/future Git history is a good anti-cheating
control.

**What remains incomplete or version-sensitive:**

- the image is tagged rather than digest-pinned and Pier is specified only as
  “newer than 0.3.0” in the README;
- independent v1.1 audit [#52](https://github.com/datacurve-ai/deep-swe/issues/52)
  reports 112/113 reference solutions passing their own verifier, one still
  failing, 122 excluded trials versus 73 explained, thin task metadata, mixed
  reasoning-effort labels, missing per-run receipts, and an unresolved timeout
  pass contradiction;
- the [v1.1 audit ledger](https://www.june.kim/auditing-deepswe-v1-1) reports
  heatmap/leaderboard denominator differences, no conflicts-of-interest
  statement, and a roughly 2.7% determinacy floor (three of 111 reviewed tasks);
- [issue #13](https://github.com/datacurve-ai/deep-swe/issues/13) describes a
  browser-session connection failure causing 99/104 attempts to fail on a task.
  Whether or not that particular v1 defect remains in v1.1, it demonstrates why
  environment/harness failures must be separate from model failures;
- public task/test/solution materials shorten contamination half-life. The
  site's “do not appear in training corpora” canary is a policy warning, not a
  technical access control; and
- current release objects, UI pages, and cached artifact snapshots can be at
  different revisions. Every cited result therefore needs a release ID,
  artifact hash/ETag, generated time, and retrieval time.

## Transferability to Sandbox

| DeepSWE practice | Sandbox decision | Why |
| --- | --- | --- |
| Versioned task manifest with source SHA, runtime image, limits, and timeout | **Transfer** | Add task ID, repo/worktree SHA, `sandbox.config.json`/image digest, host target, seed/snapshot, allowed capabilities, and finite budget. |
| Separate pristine verifier and patch application | **Transfer with adaptation** | Verify in a disposable WP/runtime instance after applying a patch; preserve the user's dirty worktree and never target production. Include regression plus task-specific behavior. |
| Public-API/observable behavior tests | **Transfer** | Prefer WordPress REST/UI/output behavior over private PHP symbol assertions; preserve browser and HTTP evidence when the reported issue is browser-rendered. |
| P2P/F2P node IDs and binary + partial reward | **Transfer as a schema, not as the verdict** | Keep named checks and fractions, but Sandbox needs `completed`, `blocked`, `unverified`, `infrastructure_error`, `timeout`, and `provider_error`; missing evidence must not become a pass. |
| Per-run trajectory, patch, raw verifier output, and CTRF-like report | **Transfer** | Store redacted, access-controlled receipts: run/job IDs, patch/diff, `sb job-status`/`job-output`, test output, WP/REST/browser evidence, snapshot ID, and verifier revision. |
| Cost, input/output/cached tokens, steps, peak context, duration | **Transfer** | Record provider/route, model snapshot, effort, pricing `as_of`, retries, polls, and parent/subagent relation. Compare within task cohorts, not raw cross-product means. |
| Deterministic subsets and repeated verifier runs | **Transfer** | Pin a benchmark release and sample seed; run gold fixtures repeatedly to detect flake before scoring. |
| Exclusion/error ledger and confidence method | **Transfer** | Keep excluded/unknown outcomes visible with reasons. Distinguish a broken Sandbox job, revision mismatch, missing artifact, and model failure. |
| OSS, no-secret, container-only assumptions | **Do not transfer** | Sandbox tasks can involve WordPress DB/uploads, browser state, remote hosts, credentials, clean URLs, and user-owned dirty worktrees. Use snapshots, least privilege, redaction, and explicit local/remote evidence. |
| Public reference solutions and hidden tests in the release | **Do not transfer** | Use synthetic/authorized fixtures or private challenge material. Never publish private code, tokens, licensing-unclear upstream content, or production data as benchmark artifacts. |
| Single `bash` harness as a model comparison | **Do not treat as Sandbox truth** | Measure both native Codex/T3/Sandbox workflows and a standardized harness; otherwise the result confounds model ability with tool scaffolding. |

### Proposed Sandbox-native run record

At minimum, each run should have:

```text
run_id, task_id, benchmark_release, parent_run_id
model_id, model_snapshot, reasoning_effort, harness, provider_route, price_as_of
repo/worktree SHA, Sandbox revision, runtime/image digest, local_or_remote target
seed/snapshot ID, allowed capabilities, request/job IDs (hashed where needed)
ordered steps: timestamp, tool namespace/name, sanitized argument signature,
             result status, exit code, error class, retry/replay relation
metrics: input/output/cached/reasoning tokens, tool calls, steps, duration, cost
receipts: patch, verifier config, test/REST/browser output, logs, evidence hashes
outcome: pass/fail/blocked/unverified/infrastructure_error/timeout/provider_error
exclusions: reason, scope, and whether the run contributes to a denominator
```

The acceptance oracle should be reconstructible from the receipt: a fresh
disposable instance applies the patch, runs task checks and regression checks,
and emits a deterministic structured result. A missing receipt, partial remote
job output, unavailable revision, or unverified browser state remains
`unknown`/`unverified`, never a successful completion.

## Licensing and data-handling decision

The repository's [Apache-2.0 license](https://raw.githubusercontent.com/datacurve-ai/deep-swe/main/LICENSE)
is scoped by [PROVENANCE.md](https://raw.githubusercontent.com/datacurve-ai/deep-swe/main/PROVENANCE.md)
to Datacurve's original task specifications, harness, verifiers, and curation.
The 113 upstream projects retain their own MIT, Apache, BSD, ISC, or other
listed terms. A repository license is not a blanket license for model
trajectories, provider-generated content, or gated exports. The HF leaderboard
mirror advertises CC-BY-4.0 but requires contact-sharing; the task mirror is
gated held-out data without a plainly displayed license. Sandbox must not copy,
train on, or redistribute those materials without a separate license/privacy
review and explicit authority.

## Recommendation

Use DeepSWE as a design reference for Sandbox's benchmark *mechanisms*: pinned
task/runtime manifests, isolated behavioral verifiers, immutable run IDs,
receipt bundles, metric semantics, deterministic sampling, and explicit
exclusion/error ledgers. Build a Sandbox-specific corpus around authorized
WordPress/runtime/browser workflows, with snapshots and remote evidence, and
keep challenge artifacts private or synthetic until licensing and privacy are
cleared. Do not call DeepSWE's public scores or artifact flags a complete
reproducibility guarantee without pinning the exact release, artifact revision,
verifier/image digest, provider route, and receipt availability.
