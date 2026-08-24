# Benchmark landscape for the Sandbox agent/tool audit

**Review date:** 2026-08-24

**Scope:** public, primary or benchmark-maintainer sources only. No benchmark
datasets, task assets, traces, or private leaderboard data were downloaded or
redistributed for this review.

This is a landscape review, not a claim that Sandbox has run any of these
benchmarks. Counts and release names are source-dated: a benchmark that moves
(`latest`, a live split, or a continuously updated task registry) must be pinned
before comparing runs.

## Executive readout

- **Closest operational analogue:** [Terminal-Bench 2.0](https://arxiv.org/abs/2601.11868)
  with the [Harbor harness](https://github.com/harbor-framework/harbor). Its
  per-trial verifier output, agent/verifier timing, usage/cost fields, and ATIF
  trajectory are the clearest model for Sandbox durable jobs.
- **Best receipt/replay patterns:** [WebArena-Verified](https://github.com/ServiceNow/webarena-verified)
  (offline network-trace replay and deterministic structural scoring),
  [tau-bench](https://github.com/sierra-research/tau2-bench) (gold-state replay
  and DB-hash outcome checks), and [ToolSandbox](https://github.com/apple/ToolSandbox)
  (state snapshots and milestone DAGs).
- **Best long-horizon stress signal:** [OSWorld 2.0](https://arxiv.org/abs/2606.29537)
  reports a median human task time of about 1.6 hours and roughly 318 tool calls
  per task for one frontier agent, then evaluates binary completion at a 500-step
  cap plus partial credit. This is a useful horizon reference, not a Sandbox
  score.
- **Best coding outcome gate:** the [SWE-bench harness](https://github.com/SWE-bench/SWE-bench)
  and its [experiments repository](https://github.com/swe-bench/experiments).
  Patch-plus-tests resolution is valuable, but the core score does not define a
  common tool vocabulary or a call/cost/time budget.
- No reviewed benchmark directly answers “how many Sandbox tool calls can be
  removed without reducing verified success.” Sandbox needs its own task corpus,
  tool taxonomy, and receipt contract; these benchmarks provide reusable fields
  and failure dimensions.

## Reading conventions

- **Native** means the benchmark or its official harness emits the field as part
  of its documented result. **Derived** means a runner can calculate it from a
  trace or log but the benchmark does not standardize it.
- A **receipt** here means a replayable, immutable-enough run record: task and
  environment identity, agent/model configuration, event/trajectory evidence,
  verifier output, artifacts, and terminal status. Most benchmarks provide
  pieces of this, not the full contract.
- “Transcript available” includes action/tool traces and environment events;
  it does not imply that hidden chain-of-thought is public or should be retained.

## Benchmark cards

### SWE-bench, SWE-bench Pro, and SWE-bench-Live (software tasks)

#### SWE-bench and Verified

**Primary sources:** [SWE-bench repository](https://github.com/SWE-bench/SWE-bench),
[SWE-bench paper](https://arxiv.org/abs/2310.06770), and the official
[experiments/submissions repository](https://github.com/swe-bench/experiments).

- **Task, environment, verifier:** a real GitHub issue plus the repository at a
  base commit is supplied to an agent; the agent emits a patch. The official
  harness evaluates the patch inside a Docker environment by running the task's
  tests and reports an instance-level resolution result. SWE-bench Verified is a
  500-problem human-confirmed solvable subset, as recorded in the official
  repository.
- **Transcript/trajectory:** the core harness requires a prediction patch and
  evaluation artifacts, not a universal trajectory schema. Official leaderboard
  submissions in `experiments` contain `trajs/` reasoning traces (when supplied)
  and `logs/` with `patch.diff`, `report.json`, and test output. The repository
  documents public log/trajectory storage and a download helper; availability is
  submission-specific and should not be assumed for every historical run.
- **Metrics:** native score is resolved rate/Pass@1 (or the selected leaderboard
  aggregate). Tool calls, LLM calls, step count, tokens, API cost, and wall time
  are **not** part of the core benchmark contract; derive them from the agent
  scaffold's trajectory/log format. The harness can re-grade saved logs without
  new containers, which is useful for receipt verification.
- **Receipt/reproducibility:** Docker images, dataset split, base commit, patch,
  test output, and run identifier form a useful partial receipt. Pin the dataset
  revision, harness commit, image digest, model/scaffold configuration, and run
  ID. The official README warns that the same `run_id` and instance can reuse a
  cached result, so a changed patch requires a new run ID.
- **Caveats and Sandbox transfer:** static issue/PR mining creates contamination
  and stale-environment risks; hidden tests can make a passing local test
  insufficient; traces may be free-form text rather than structured tool events.
  Use SWE-bench as a **verified outcome gate** for a Sandbox coding task, while
  collecting Sandbox-native command events and cost/time separately.

#### SWE-bench Pro

**Primary sources:** [Scale Labs project page](https://labs.scale.com/papers/swe_bench_pro),
[paper](https://arxiv.org/abs/2509.16941), [official public repository](https://github.com/scaleapi/SWE-bench_Pro-os),
[public leaderboard/methodology](https://labs.scale.com/leaderboard/swe_bench_pro_public),
and the linked [trajectory viewer](https://docent.transluce.org/dashboard/032fb63d%2D4992%2D4bfc%2D911d%2D3b7dafcb931f/agent_run).

- **Task, environment, verifier:** 1,865 problems across 41 repositories are
  partitioned into 731 public, 858 held-out, and 276 commercial/private tasks.
  The public repository describes multi-file, long-horizon issue/feature work;
  Scale's methodology describes reproducible Docker environments, fail-to-pass
  and pass-to-pass tests, and human augmentation/verification. The output is a
  patch evaluated by the supplied run scripts.
- **Transcript/trajectory:** published runs expose trajectories through the
  public trajectory viewer, and the repository contains `traj/` material for
  included scaffolds. This is not a requirement that every arbitrary run be
  archived, nor a fixed event schema.
- **Metrics:** public scoring is resolution/Pass@1. Turn limits are configurable
  in the scaffold, but the benchmark does not standardize tool-call count,
  prompt/completion tokens, API cost, or end-to-end wall time. Those are
  runner-derived metrics.
- **Receipt/reproducibility:** each instance has a Docker image/tag and run
  scripts; a reproducible public run should retain the exact dataset revision,
  image tag/digest, scaffold/model config, patch, test report, and trajectory.
  Held-out and commercial tasks cannot be independently reproduced. The public
  repository currently records test/leaderboard corrections, so pin a commit and
  do not compare floating results as if they were one release.
- **Caveats and Sandbox transfer:** the private/held-out split is a strong
  contamination/generalization test but is not a public receipt. Cloud Modal or
  local Docker resource differences can change runtime. Pro is the best external
  analogue for Sandbox’s “hours-to-days, multi-file change” work, but its score
  must be joined to a Sandbox event ledger for call reduction.

#### SWE-bench-Live

**Primary sources:** [official site/leaderboard](https://swe-bench-live.github.io/),
[Microsoft repository](https://github.com/microsoft/SWE-bench-Live),
[paper](https://arxiv.org/abs/2505.23419), and the official
[submission/trajectory repository](https://github.com/SWE-bench-Live/submission).

- **Task, environment, verifier:** an automatically curated, continuously
  updating stream of real GitHub issue-resolution tasks. The initial paper
  release contained 1,319 tasks from 93 repositories; the current project also
  publishes multi-language and Windows variants. Each instance has a dedicated
  Docker execution image. The agent emits a patch and the evaluator runs the
  selected tests.
- **Transcript/trajectory:** the submission repository stores predictions,
  evaluation results, and optional `logs/`/`trajs/` rollouts. The public site
  states that submitted trajectories are archived. There is no single required
  trajectory format across agents.
- **Metrics:** native leaderboard metric is resolved rate over a selected split.
  Step/tool count, tokens, API cost, and wall time are optional scaffold fields,
  not a common score. The site distinguishes frozen Lite/Verified splits from
  changing Full/Test splits; record the split and date.
- **Receipt/reproducibility:** pin the dataset snapshot, repository commit,
  Docker image, OS/language variant, agent/model/scaffold, and test report. The
  moving test split is intentionally fresh; a floating “latest” result is not a
  reproducible comparison. Submission guidance asks for rollout count,
  iterations, and experimental settings, which are useful receipt metadata.
- **Caveats and Sandbox transfer:** freshness improves contamination resistance
  but introduces drift in repositories, dependencies, and test validity. Windows
  tasks may require a Windows-specific agent. Live is a good model for a
  periodically rotated Sandbox holdout, provided every task is frozen by an
  immutable snapshot before a run.

### Terminal-Bench 2.0 (terminal agents)

**Primary sources:** [Terminal-Bench 2.0 paper](https://arxiv.org/abs/2601.11868),
[benchmark repository](https://github.com/harbor-framework/terminal-bench-2),
[current task repository](https://github.com/harbor-framework/terminal-bench),
[official Harbor harness](https://github.com/harbor-framework/harbor),
[Harbor quickstart/result format](https://github.com/harbor-framework/docs/blob/main/quickstart.mdx),
and the [task template](https://github.com/harbor-framework/terminal-bench/blob/main/docs/task-template.toml).

- **Task, environment, verifier:** the paper describes 89 hard tasks, each with
  a unique computer-terminal environment, a human-written solution, and tests.
  Harbor runs each task in an isolated Docker/cloud environment. Task metadata
  declares agent, verifier, build, CPU/memory/storage/GPU, and network timeouts;
  the verifier runs separately and reads declared artifacts, then writes a
  reward (binary or float).
- **Transcript/trajectory:** Harbor stores per-trial `agent.log`, `verifier.log`,
  `result.json`, `reward.txt`, and `trajectory.json` (ATIF). The official docs
  describe trace export and resumable jobs. This is the strongest reviewed
  public format for replayable terminal-agent behavior.
- **Metrics:** aggregate success rate and mean reward are native. The documented
  result shape includes `agent_time_sec`, `verifier_time_sec`, prompt/completion
  token counts, total cost, and per-trial reward; attempts, task timeouts, and
  concurrency are also explicit run controls. Treat usage/cost fields as
  **available when the selected agent/provider populates them**, not as a promise
  that every adapter reports identical accounting.
- **Receipt/reproducibility:** tagged dataset releases, Docker task definitions,
  oracle solutions, task configs, verifier logs, reward files, and ATIF traces
  make a good receipt. The current repository recommends running the oracle five
  times because environment flakes are possible. Pin the dataset tag, task
  commit, harness commit, image/provider, model/scaffold, attempts, timeout, and
  network policy.
- **Caveats and Sandbox transfer:** it is a continuous benchmark, so `latest`
  drifts; tasks can use public network access; provider, model, and agent updates
  affect outcomes. Reward is task-specific and not necessarily comparable across
  task authors. Harbor is nevertheless the best template for Sandbox’s durable
  job lifecycle: accepted job ID, append-only event stream, bounded retained
  output, verifier receipt, and terminal status.

### BrowserGym and WebArena (browser agents)

#### BrowserGym framework

**Primary sources:** [BrowserGym repository](https://github.com/ServiceNow/BrowserGym),
[API documentation](https://browsergym.readthedocs.io/latest/api.html),
[BrowserGym ecosystem paper](https://arxiv.org/abs/2412.05467), and the
[AgentLab runner](https://github.com/ServiceNow/AgentLab).

- **Task, environment, verifier:** BrowserGym is an extensible Gymnasium-style
  environment, not one fixed task set. A task implements `AbstractBrowserTask`
  setup/validation and an agent interacts with a Chromium environment using a
  high-level browser action string. `reset(seed)` and `step(action)` return
  observations, reward, termination/truncation, and diagnostic `info`. It hosts
  WebArena, WebArena-Verified, WorkArena, MiniWoB, VisualWebArena, and others.
- **Transcript/trajectory:** core `step` events are available to a caller, and
  BrowserGym publishes traces from the ecosystem paper. AgentLab persists
  screenshots/actions/step metadata and provides AgentXray trace inspection.
  Trace retention is runner/study-dependent rather than a guarantee for every
  leaderboard row.
- **Metrics:** step count, reward, termination/truncation, and task-specific
  `info` diagnostics are native. AgentLab's benchmark table publishes task and
  max-step settings (for example, WebArena 30). Token/API cost and wall time are
  study-derived, not a common BrowserGym score.
- **Receipt/reproducibility:** AgentLab records benchmark/package versions,
  commit hash, seed, and study metadata and offers a re-run comparison agent.
  Pin Playwright/browser/package versions, task seed, benchmark commit, model
  configuration, and website snapshot. Do not treat a live website URL as an
  immutable environment.
- **Caveats and Sandbox transfer:** the maintainers document changing browser
  dependencies, silently updated API models, live websites, stochastic agents,
  task dependencies, and expensive instance resets. A maintainer discussion also
  records that original main-table traces were not retained, while later
  ecosystem traces were released. BrowserGym is therefore a good action-event
  schema and reproducibility journal, but not by itself a receipt guarantee.

#### WebArena and WebArena-Verified

**Primary sources:** [canonical WebArena repository](https://github.com/web-arena-x/webarena),
[WebArena paper](https://arxiv.org/abs/2307.13854), and the
[WebArena-Verified repository](https://github.com/ServiceNow/webarena-verified).

- **Task, environment, verifier:** WebArena is a self-hosted collection of 812
  natural-language, multi-step tasks over functional replicas of shopping,
  shopping-admin, Reddit, GitLab, Wikipedia, and map services. The agent emits
  browser actions; functional evaluators check the resulting site/database state
  and answer. The canonical README instructs operators to reset the environment
  after the 812 examples.
- **Transcript/trajectory:** the canonical runner saves a per-task HTML
  trajectory. Official releases include human recordings for approximately 170
  tasks and execution trajectories. WebArena-Verified accepts captured network
  traces and can evaluate offline, while removing LLM-as-judge and substring
  scoring in favor of type-aware normalization and structural comparison.
- **Metrics:** end-to-end task success is native; AgentLab documents a 30-step
  max for WebArena. Action count, browser wait time, LLM calls, tokens, and cost
  can be derived from runner logs but are not part of the original score.
- **Receipt/reproducibility:** self-host the site containers, pin the task/config
  version and browser stack, capture the action/network trace, and reset state
  between dependent tasks. WebArena-Verified's 812-task full set and 258-task
  hard subset provide a more explicit versioned/offline replay path.
- **Caveats and Sandbox transfer:** live browser state can be corrupted by task
  ordering; AgentLab notes dependency-aware scheduling and resets that can take
  about five minutes. Demo hosts are not the evaluation environment. Use the
  Verified network-trace pattern for `visit` receipts, and count each browser
  action separately from page-load/wait events so a single high-level action does
  not hide many network calls.

### OSWorld and OSWorld 2.0 (computer-use agents)

**Primary sources:** [OSWorld 1.0 repository](https://github.com/xlang-ai/OSWorld),
[OSWorld 1.0 paper](https://arxiv.org/abs/2404.07972), [OSWorld 2.0 repository](https://github.com/xlang-ai/OSWorld-V2),
[OSWorld 2.0 paper](https://arxiv.org/abs/2606.29537), and the
[OSWorld 2.0 release manifest guidance](https://github.com/xlang-ai/OSWorld-V2/blob/main/benchmark_releases/README.md).

- **Task, environment, verifier:** OSWorld 1.0 contains 369 real desktop/web
  tasks in Ubuntu/Windows/macOS-style VM environments, using screenshots and/or
  accessibility observations with mouse, keyboard, or computer-use actions.
  Execution-based evaluators inspect application/filesystem state. OSWorld 2.0
  has 108 long-horizon workflows across everyday and professional work and uses
  stateful VM/provider environments with the same execution-based principle.
- **Transcript/trajectory:** OSWorld 1.0 results include screenshots, actions,
  and video recordings. Public leaderboard verification asks maintainers to run
  the agent or receive monitoring data and trajectories. OSWorld 2.0 links an
  official trajectory viewer/download and supports manual trajectory inspection;
  task classes/assets are gated to reduce benchmark leakage.
- **Metrics:** v1 exposes `max_steps`, per-domain and overall success/score, and
  saved action/media artifacts. The OSWorld 2.0 paper reports a median human time
  of about 1.6 hours, an average of about 318 tool calls for Claude Opus 4.7,
  binary completion at a 500-step cap, and partial score. API/token cost is not a
  universal benchmark field; derive it from model/provider logs.
- **Receipt/reproducibility:** v1 requires provider, VM/image, observation/action
  mode, model, max steps, and result directory. V2 explicitly requires one
  release-aligned set of code tag, task files, gated assets, mocked websites, and
  provider images; the current repository recommends the pinned
  `osworld-v2-2026.08.08` release and warns against `main`/`latest`. Record the
  release manifest and asset/image digests in every receipt.
- **Caveats and Sandbox transfer:** VM providers, GPU/KVM support, browser/proxy
  behavior, Google/GitLab credentials, and OS-specific timing can dominate
  results. V2's gated assets and hosted websites make independent replay harder;
  credentials must never enter a receipt. The 500-step/partial-score design is a
  useful model for Sandbox horizon budgets and checkpoint artifacts, but desktop
  screenshots are not a substitute for Sandbox command/verifier evidence.

### τ-bench (τ²/τ³ repository; tool-agent-user interaction)

**Primary sources:** [current τ-bench repository](https://github.com/sierra-research/tau2-bench),
[original τ-bench paper](https://arxiv.org/abs/2406.12045), the
[task/evaluation schema](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md),
and the [run/output guide](https://github.com/sierra-research/tau2-bench/blob/main/docs/getting-started.md).

- **Task, environment, verifier:** a simulated user (usually another LLM) and a
  tool-using customer-service agent interact under a domain policy. Domains in
  the current repository include airline, retail, telecom, mock, and a knowledge
  domain; voice/full-duplex is also supported. Tasks provide tools, policy,
  user instructions, and evaluation criteria. The default text reward combines
  database end state (`DB`) and required communication (`COMMUNICATE`); the
  evaluator replays one reference action list in a fresh gold environment to
  derive a target DB state, then accepts any equivalent agent trajectory.
- **Transcript/trajectory:** text runs persist `results.json` containing task and
  simulation data. Voice runs additionally persist per-simulation JSON, task logs,
  audio, tool-call labels, and optional LLM debug logs. `tau2 view` inspects
  individual simulations and metrics. Tool/turn counts are available from the
  simulation event stream, not as a single cross-run metric contract.
- **Metrics:** final reward is the product of configured reward components;
  `partial_action_reward` is a diagnostic similarity to one reference path and
  must not be mistaken for correctness. The original paper adds `pass^k` across
  repeated trials to expose reliability. Token cost, API cost, and wall time are
  runner/provider-derived (LLM debug logs can support accounting), not the core
  leaderboard score.
- **Receipt/reproducibility:** pin the repository/tag, task split, domain policy,
  task fixture, agent and user models, prompts, sampling settings, max turns,
  number of trials, and database snapshots. Replay the gold actions and retain
  predicted/final DB hashes, communication checks, reward basis, and simulation
  JSON. The current README records a v1.0.1 grading correction and explicitly
  warns that pre/post-fix scores are not comparable.
- **Caveats and Sandbox transfer:** user-simulator/model stochasticity and
  evolving task fixes make raw scores drift. A reference action list is not an
  efficiency target; many tasks intentionally give full reward for a correct
  refusal with no writes. Use τ-bench's state and policy checks to test Sandbox
  mutating commands, refusal behavior, and `pass^k` reliability—not to enforce a
  prescribed command sequence unless the task truly has one safe path.

### ToolSandbox (stateful tool-use alternative)

**Primary sources:** [Apple ToolSandbox repository](https://github.com/apple/ToolSandbox)
and the [ToolSandbox paper](https://arxiv.org/abs/2408.04682).

- **Task, environment, verifier:** scenarios define a Python tool allow-list,
  initial world state, user/agent/system roles, optional distraction or
  scrambled tools, and a milestone DAG. The execution context snapshots world
  state and the dialog at every turn. Evaluation matches intermediate/final
  milestones with database snapshot, add/remove/update, tool-trace, guardrail,
  and text-similarity functions, respecting DAG ordering; many different tool
  paths can pass.
- **Transcript/trajectory:** each run writes `result_summary.json` and a full
  `trajectories/<scenario>/conversation.json`; tool traces and per-turn state
  snapshots are retained. The example result exposes turn count, milestone
  mapping, and similarity values.
- **Metrics:** native signals are per-milestone similarity in `[0,1]`, aggregate
  trajectory similarity, `turn_count`, and milestone mapping. There is no
  standardized API cost, token, or wall-time field; derive those from model/API
  logs and event timestamps.
- **Receipt/reproducibility:** scenario definition, initial/final state snapshots,
  tool allow-list, milestone DAG, conversation, and result summary form a strong
  logical receipt. Pin the repository commit and model/provider configuration.
  The project README notes that tool execution currently runs directly on the
  host (not a Docker sandbox) and that some scenarios use RapidAPI, so host and
  network state must be treated as part of the environment.
- **Caveats and Sandbox transfer:** the implementation targets an older Python
  stack and external APIs; host execution is not a safe isolation model for
  untrusted tools. Similarity can accept an equivalent path and is not a
  minimum-call proof. ToolSandbox is nevertheless an excellent pattern for
  Sandbox state-transition receipts: record before/after state hashes, mutation
  guardrails, milestone dependencies, and every tool error without exposing
  secret values.

## Transfer matrix to Sandbox

The matrix separates what can be adopted as a contract from what is only an
optional benchmark-specific convenience.

| Sandbox audit dimension | Strongest public analogue | Transfer to Sandbox | Acceptance evidence to require |
| --- | --- | --- | --- |
| Verified outcome | SWE-bench patch + held-out tests; Terminal-Bench verifier; WebArena/OSWorld execution checks | Every task must end in a typed verifier result; agent self-report is never success | `verifier_id`, exit/status, structured assertions, retained stdout/stderr, artifact hashes, terminal receipt |
| Tool/step count | Harbor ATIF; BrowserGym `step`; WebArena max steps; OSWorld 2.0 500-step cap; τ/ToolSandbox turn/tool traces | Count each Sandbox command/tool invocation and each retry separately; keep semantic action count distinct from transport/poll calls | `tool_call_count`, `retry_count`, `poll_count`, `step_budget`, event sequence with monotonic timestamps |
| Long-horizon behavior | SWE-bench Pro hours/days; OSWorld 2.0 318-call average; ToolSandbox milestone DAG | Add checkpoint/milestone assertions so a partial run can be diagnosed without declaring success | milestone IDs, before/after state digest, checkpoint artifact, partial/terminal status, remaining budget |
| Cost and latency | Harbor's documented agent/verifier time and token/cost fields; other benchmarks derive these | Make accounting first-class and provider-neutral; never infer cost from wall time alone | `agent_time_ms`, `verifier_time_ms`, queue/transport time, prompt/completion tokens when available, `cost_usd` plus `unknown` reasons |
| Replay and idempotency | WebArena-Verified offline network traces; τ gold DB replay; ToolSandbox snapshots | Reuse one request identity for safe replay; preserve the original job and receipt when the caller disconnects | pinned task/env/model refs, `request_id`, durable `job_id`, event cursor, replay result, no duplicate side effect |
| Environment provenance | SWE Docker images; Harbor task TOML; OSWorld V2 release manifest; AgentLab study metadata | Treat checkout/image/provider/website/seed as required receipt fields, not prose | commit/tag/digest, dependency lock hash, provider, seed, network mode, task snapshot, schema version |
| Reliability | τ `pass^k`; Terminal-Bench repeated oracle; BrowserGym seeds; WebArena reset/dependency scheduling | Run bounded repeated trials and report variance; distinguish model drift from Sandbox regressions | attempt number, seed, pass^k/CI, reset outcome, flaky/verifier error class, per-attempt receipts |
| Failure and degradation | Harbor verifier logs; SWE logs/test output; OSWorld partial score; ToolSandbox milestone similarity | Preserve partial evidence on timeout/cancellation and label unknown/unavailable; do not turn missing output into zero | typed failure reason, completed evidence, timeout/cancel state, `unknown` coverage, bounded retained logs |
| Safety and disclosure | τ policy/DB checks; ToolSandbox guardrails; OSWorld credential caveats; WebArena trace capture | Redact secrets and private paths in event receipts; require capability checks before mutating tools | redaction status, capability decision, safe argument summary, no raw token/password, immutable audit record |

## Recommended Sandbox measurement contract

For a future native Sandbox agent benchmark, retain one JSONL event envelope per
tool call and one terminal receipt per task. The minimum fields should be:

```text
task_id, task_revision, environment_revision, provider, seed,
agent_id, model_id, scaffold_revision, request_id, job_id,
event_index, parent_event_index, monotonic_start_ms, monotonic_end_ms,
tool_name, safe_argument_summary, result_status, exit_code,
stdout_ref, stderr_ref, artifact_refs, state_before_digest, state_after_digest,
verifier_id, verifier_result, retry_count, poll_count,
prompt_tokens, completion_tokens, cost_usd, unknown_reasons
```

Rules implied by the reviewed benchmarks:

1. Pin a task/environment release before a run; never use a floating `latest` as
   comparable evidence.
2. Separate model/scaffold/tool transport from the task outcome. A lower call
   count is only a win if the verifier result and receipt completeness remain
   unchanged.
3. Make receipt replay read-only by default. Replaying a lost submission must
   use the same `request_id`; a second identity is not an experiment retry.
4. Keep full traces optional and redacted, but keep a structured event summary
   and verifier evidence mandatory. Raw chain-of-thought is neither required nor
   a safe substitute for action receipts.
5. Report `unknown` for missing timing, token, cost, or coverage data. Do not
   coerce unavailable accounting into zero.

## Bottom line

Adopt Harbor's per-trial durable evidence and ATIF-shaped event stream first;
adopt WebArena-Verified/τ replay ideas for deterministic state receipts; and use
OSWorld 2.0's explicit horizon/partial-score framing to stress long Sandbox
jobs. Keep SWE-bench-family results as a patch-and-test outcome gate. A Sandbox
claim about fewer tool calls is admissible only when the task revision,
environment, verifier, receipt, and cost/time accounting are all pinned and
available for independent replay.
