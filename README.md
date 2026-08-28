# Sandbox

A real WordPress environment for designers, developers, and QA at WPDeveloper —
drivable by Claude Code (or any MCP client: Cursor, Cline, Continue, Zed).

## Scoped recovery

Recovery is profile-driven through `sb recovery`. Capture, restore apply, retention deletion,
and schedule activation are protected; see [docs/recovery.md](docs/recovery.md).

## Extension boundaries

Sandbox keeps public CLI and MCP behavior stable while feature ownership is modularized:

- project descriptors select `kind` before runtime-specific defaults; omitted `kind` remains `wordpress`;
- registry identity and atomic persistence live behind the project-registry repository;
- runtime capabilities reject unsupported work before process, network, proxy, or registry side effects;
- CLI commands and MCP tool groups are owned by explicit deterministic manifests;
- shared process, HTTP, port, path, and proxy services own mechanisms, while adapters own runtime policy;
- Hermes state, routing, jobs, gateway, and backup planning are bounded modules.

`sandbox_core.py`, `sandbox.registry.COMMANDS`, `sandbox.hermes.facade`, and the MCP
`app.py` helper namespace are compatibility/rollback paths, not extension points.
New code must use the bounded service or registration contract. Their consumer sets
are frozen by architecture tests; removal requires parity evidence and separate
human approval.

## WordPress runtime selection

Compose remains the only automatic/default WordPress runtime. A gitignored machine override
may explicitly select a supported native adapter; detection never opts a project in. Managed
Ubuntu execution is advertised only after effective namespace, mount, network, credential,
resource, and hostile-path proofs pass. Herd, Valet, and declared POSIX profiles are labeled
`trusted_shared_host` and are intended only for trusted project code.

> **Compose trust boundary:** Use Compose only with trusted project, plugin, and
> agent-generated code. Docker containers and workspaces share the host kernel and Docker
> daemon; this is not a hostile-code or multi-tenant security boundary. No per-instance
> deny-by-default egress policy exists.

Inspect support without mutation:

```bash
./sb native support --json
./sb native preflight --project-dir . --json
./sb native install-plan --project-dir . --web-server nginx --json
```

Native package installation is interactive-only. Instance plugins, CLI, tests, Composer, and
jobs never fall back to host execution when managed-native isolation is selected. See
[`docs/native-runtime-isolation.md`](docs/native-runtime-isolation.md) for guarantees,
limitations, egress grants, evidence, and recovery.

**CLI-first, per-project, and MCP-optional.** Each plugin repo carries its own
`sandbox.config.json`. You `cd` into a plugin, and a single MCP server boots a
WordPress instance for that directory on demand and runs the plugin's **real
phpunit tests** — no central catalog, nothing to pre-register.

---

## Get started

> **Note:** This is a major rewrite to the per-project model hosted at
> [`alimuzzaman/sandbox`](https://github.com/alimuzzaman/sandbox). Install:

**Prerequisites:** A running Docker-compatible engine (Docker Desktop or
OrbStack on macOS) · Python 3.9+ · Claude Code (or any MCP client). On a fresh
machine, run the OS bootstrap script first:

```bash
# macOS
bash scripts/install-macos.sh   # Homebrew → python3 → Docker Desktop/OrbStack → Reader.md

# Ubuntu / Debian
bash scripts/install-ubuntu.sh  # apt (python3+venv) → Docker CE

# Arch Linux (and derivatives: Manjaro, EndeavourOS)
bash scripts/install-arch.sh    # pacman → python → docker + docker-compose
```

Other Linux distros (Fedora/RHEL, openSUSE, etc.) work too — `./sb setup`
detects `dnf`/`zypper` and offers the right install commands automatically;
there's just no dedicated one-shot bootstrap script for them yet. **Windows**
isn't supported natively (the CLI is a POSIX shell + Python tool, and relies
on Docker Unix sockets and process groups/signals) — run it inside **WSL2**
instead, where it behaves exactly like the Ubuntu path above.

**Clone and set up:**

```bash
git clone -b main https://github.com/alimuzzaman/sandbox.git
cd sandbox
./sb global           # puts `sb` on your PATH (do this first)
./sb --version        # report the checked-in CLI version without setup/mutation
./sb setup            # prepares the CLI and local runtime
./sb guide            # show the runtime-aware CLI catalog
./sb domains setup    # optional: clean no-port URLs → https://<name>.<tld>
```

`setup` offers to install missing prerequisites (default always **No**)
and never needs `sudo` for the base install.

`sb setup` is registry-wide and cannot be targeted with `--instance` or the
routing `--label`. To reconcile an existing named instance, use
`sb apply --instance NAME`; to prepare one project, use the project-scoped
`sb ensure --project-dir DIR`.

`sb ensure` is project-scoped and refuses `--instance NAME`, which the shared
parser cannot otherwise use safely. Select the project with `--project-dir`
and, when it owns more than one instance, `--label LABEL` (add `--create` when
minting that label). Reconcile an existing named instance with
`sb apply --instance NAME`.

`sb init` follows the same project-scoped boundary and refuses `--instance
NAME`; pass `--project-dir DIR` so an initializer cannot mutate the tooling
checkout by mistake. For an additional labeled instance, run
`sb ensure --project-dir DIR --label LABEL --create` explicitly.
Init treats that exact directory (or the exact current directory when omitted) as its
maximum root, does not inherit ancestor project markers, and refuses the user home itself.

On macOS, the bootstrap also installs [Reader.md](https://github.com/jnahian/reader.md)
by default when Homebrew is available. It provides the `reader` command for
opening local Sandbox documentation and read-only remote documentation folders.
Set `SANDBOX_SKIP_READER_MD=1` before running the bootstrap to opt out; a
Reader.md failure only warns and never prevents Sandbox setup.

Reader.md is maintained in its own Homebrew tap. The bootstrap scopes
Homebrew's required trust grant to its `reader-md` cask before installation;
review that upstream tap if your environment disallows third-party casks.

### Reader.md for agents and operators

Reader.md is an optional **local, visual reading surface**. An agent on the
macOS workstation may open a known local Markdown file or folder when that
helps the operator review documentation:

```bash
reader /absolute/path/to/spec.md
reader /absolute/path/to/folder
```

It is not an MCP server and its window is not evidence an agent can inspect.
Use `fs_read`, repository reads, or `ssh` for machine-readable evidence and
tests. Do not use `reader remote` or `reader rm` from an agent: the former
adds an SSH-backed application connection and the latter removes saved Reader
configuration. Those remain explicit operator commands. `reader ls` is safe
for an operator to inspect configured Reader roots.

New projects whose hostname is omitted use the standards-reserved **`.test`**
suffix. Existing persisted names—including `.tst`—are preserved. Use
`./sb domains status --project-dir . --json` to see the requested name, source,
active resolver, actual and expected address, ownership, health, and fallback.
Sandbox never creates a local override for a public FQDN or a new `.local` name.

A project can pin its own TLD with `"tld": "<your-tld>"` in its
`sandbox.config.json` (overrides the prompt for that project):

```jsonc
// sandbox.config.json
{ "domains": { "tld": "test", "strategy": "systemd-resolved" } }
```

`domains setup` is optional — without it, instances still work at
`http://localhost:<port>`.

Running `./sb global` first means the MCP registration uses `sb` (PATH-based,
like `@wordpress/env`) rather than a hardcoded absolute path — so the
registration survives the repo being moved or re-cloned.

`setup` registers **one** MCP server named `sandbox` at user scope so
**every** `claude` session on the machine has it — from any directory:

```bash
claude          # in any project, in any dir
```

That single server routes by the `project_dir` every tool receives — there are
no per-instance servers to manage.

---

## The per-project model

A plugin repo carries a **`sandbox.config.json`** describing its stack:

```jsonc
{
  "plugins":   ["."],                 // this repo; sibling slugs/paths/zip-URLs for addons
  "mappings":  { "wp-content/plugins/elementor-pro": "/abs/path" },
  "phpVersion": null,                 // null → wordpress:latest; e.g. "8.1"
  "wpVersion":  null,                 // EXACT pin ("6.4" = 6.4.0, not 6.4.x).
                                      // Leave null unless one build is required.
  "server":     "apache",             // apache | nginx | litespeed
  "config":     { "WP_DEBUG": true }, // → wp-config constants
  "tests":      { "suite": "auto" }   // auto-detect WP_UnitTestCase vs Brain/Monkey
}
```

(An existing **`.wp-env.json`** is read as a fallback and converted on
`sandbox init`. Full schema: [`docs/sandbox-config-reference.md`](docs/sandbox-config-reference.md).)

Generic PHP, JavaScript/Node, Docker, Laravel/Sail, Astro, and similar projects
can use the same framework-neutral Compose runtime by declaring `kind: compose`
and their public service in `sandbox.config.json`. See the
[generic Compose configuration reference](docs/sandbox-config-reference.md#generic-compose-projects).

Then, from the plugin directory:

```bash
cd ~/dev/embedpress
sandbox init      # scaffold sandbox.config.json (or convert .wp-env.json),
                  #   boot a per-directory instance, provision the test harness
sandbox test      # auto-select unit or integration mode and run PHPUnit
sandbox test unit       # pure PHPUnit; skips WP suite, polyfills, and test DB
sandbox test integration # externally-provisioned WP suite + isolated test DB
sandbox ensure    # just boot/refresh this project's instance (create-if-missing)
```

`init` is the one command from a bare checkout to a running, testable stack.
Each project gets **one instance by default**, keyed by its directory and
tracked in an on-disk registry. Sibling plugins listed in one config share
that instance. A project can also own additional labelled instances side by
side (e.g. to test a second PHP/WP version, or a zip install alongside dev) —
pass `--label <name>` / `label=` (default `default`); see
`docs/multi-instance-spec.md`.

**With Claude, you don't even run those** — the MCP tools take `project_dir`
(the agent passes your plugin dir), and `ensure_instance` boots on demand. Just
work in the plugin and ask Claude to test/fix/build.

### The test harness (the core value)

Sandbox provides the WP test suite, phpunit, the Yoast polyfills, composer, and
an isolated `wp_tests` database **externally** for integration tests — mounted
only at test time — so a plugin's `composer.json` stays clean. `sandbox test`
resolves `tests.suite` (`auto`, `unit`, or `integration`); auto selects unit only
for unambiguous Brain/Monkey-only evidence and conservatively selects integration
otherwise. Unit mode uses project Composer dependencies and PHPUnit without the WP
suite, polyfills, test DB, or `WP_TESTS_*` environment. The `run_tests` MCP tool
accepts the same optional `mode` and returns the resolved mode with its summary.

Version pins resolve server-aware: `phpVersion: "8.1"` boots `wordpress:php8.1`
on apache, the `-fpm` flavor on nginx, and an OpenLiteSpeed `lsphp81` image on
litespeed; the wp-cli container (where tests run) follows the PHP pin too.

---

## Plain Claude vs. Claude + sandbox

Claude in your IDE is already smart. It can read your code, propose diffs, talk
through architecture. What it **cannot** do alone is run your WordPress, see
what your block actually renders, query your DB, check `debug.log`, or know your
plugin's specific conventions. It's a brilliant pair-programmer working
blindfolded against an unfamiliar codebase. The sandbox removes the blindfold
and hands it the keys.

### What plain Claude has

- Your source code on disk (Read / Write / Edit).
- The internet (web search, fetch).
- Its training knowledge of WordPress / PHP / JS.
- Nothing about *your* WordPress, *your* plugin's conventions, or whether the
  edit it just made actually works.

### What Claude + sandbox has, on top of that

- **A live WordPress** with your plugin symlinked in. Edits land in seconds, no
  rebuild. The agent acts on the stack instead of guessing at it.
- **Real tests on demand** — `run_tests` runs the plugin's phpunit suite against
  an externally-provisioned WP test harness, so "it works" is backed by a green
  run, not a `php -l`.
- **Your plugin's institutional knowledge** auto-loaded. The project's
  `CLAUDE.md` (textdomain rules, `save()` BC traps, build conventions,
  task-tracker board, sister-repo location) reaches the model via `focus_get`.
- **A compact operating prompt** in every Claude session via the MCP
  `instructions` field — reflexes ("first tool call reproduces, not Read"),
  anti-patterns ("declaring fixed from code reading"), the project handshake
  (always pass `project_dir`; call `ensure_instance` first). Deeper guidance
  loads on demand via `load_context` / `load_skill(name)`.
- **Skills + workflows** for the patterns that repeat: `fix` for bugs (one-pass
  loop with paired before/after evidence), `build-feature` for new features
  (three-phase, size-scaled gates), `wp-pilot` for browser-driven admin testing,
  `fluentboards` for task management.

### What that means on three tasks you actually do

**Fix a bug in your plugin.**

| Step | Plain Claude | Claude + sandbox |
|------|--------------|------------------|
| **Understand** | Asks you the version, the active plugins, the theme. | The project's `CLAUDE.md` is already in context; can fetch the task-tracker card via REST in one call. |
| **Reproduce** | "Let me look at the file" → guesses the cause; can't verify. | First tool call provisions whatever the bug needs and triggers it on the live WP; captures the real error as `EVIDENCE.before`. |
| **Find every site** | Reads the file the report names; misses the Pro-side mirror. | Greps every call site across the plugin AND its `-pro` sibling in one pass. |
| **Fix** | Edit, ask you to test, edit again. 3–5 rounds. | Batch-edits every affected file in one pass. |
| **Verify** | "Looks right," or `php -l`. | Re-triggers the failing call → confirms the output flipped → `EVIDENCE.after`. Or `sandbox test` → green. |
| **Ship** | Stops at the working tree. | Commits and pushes verified completed work on the active branch automatically. |

**Build a new feature.** `load_workflow('build-feature')` → Phase 1 ESTABLISH
(verb-led title, size class, live-verifiable success criteria, out-of-scope,
edge cases) → Phase 2 PLAN (reuse audit naming every existing helper/table/route
it'll ride on; cross-surface grep) → Phase 3 BUILD (vertical slices, each
verified by an `sb` CLI/MCP call; non-negotiables — auth, sanitize-in/escape-out, slug
prefixing — enforced per Edit). Final `STATUS: SHIPPED` block pairs every
success criterion with live evidence + rollout notes.

For material or ambiguous work, Spec Kit can begin one stage earlier:
`speckit-refine` creates and repeatedly tightens a single `prd.md`, preferring
Terra Medium for drafting and requiring an independent Sol High validation before
readiness. It cannot create specifications, plans, tasks, or code. A validated PRD
marked `READY FOR SPECKIT` is consumed in place by Sol Medium `speckit-specify`,
preserving the numbered feature directory. The normal clarify, plan, tasks, and
analyze stages remain required before implementation; implementation prefers Terra
High, or Sol Medium for architecture-sensitive or cross-cutting work.

The resulting handoff is deliberately phase-specific: Terra Medium drafts product
intent, Sol High validates and strengthens the ready PRD, Sol Medium creates the
formal specification, and Terra High implements the approved task plan. A named
model preference is a task-launch default, not an implicit root-model switch; a
fallback must be disclosed and cannot be represented as a completed Sol validation.

```text
speckit-refine → Sol High validation → speckit-specify → speckit-clarify
→ speckit-plan → speckit-tasks → speckit-analyze → speckit-implement
```

**Verify a UI flow.** `visit` is URL-scoped (WordPress or generic Compose), opens
a real admin or frontend URL, and returns a screenshot, DOM, and console errors
without you switching tabs.

### The two underlying patterns

1. **Live evidence is the only evidence.** Every "fixed" / "shipped" /
   "verified" is backed by an `sb` CLI/MCP call (or a test run) against the running
   WordPress — not a claim from reading code.
2. **Verified changes ship as a normal Git update.** Sandbox commits and pushes
   the active branch after required checks. Force-pushes, tags, releases,
   deployments, and PR actions remain explicit.

---

## CLI-first operation (MCP optional)

### Safe secret inspection and use

Use the registered-source secret broker to list key names or structured key
paths across dotenv, JSON, INI, properties, TOML, YAML, XML, PEM, opaque-token,
and binary-container sources. Before parsing, `secrets source-info` can report
whether the registered file exists, whether it is empty, its type, a size
bucket, and whether the broker can safely open it—without reading its contents
or returning its path. It can validate or apply a fixed mask to an
eligible scalar, run a bounded trusted child without displaying the credential,
and update one dotenv assignment through protected input. Plaintext reveal is a
human-only local TTY exception and is never available through MCP. See
[Safe secret inspection](docs/secret-inspection.md) or load the
`secret-inspection` skill for the least-disclosure workflow and incident steps.

### Host storage monitoring and safe cleanup

Inspect local or named-remote storage without booting an instance:

```sh
./sb resources status --json
./sb resources status --remote scaleway-sandbox --thorough --budget 60 --json
./sb resources status --remote scaleway-sandbox --deep --budget 600 --json
# whole-host attribution in one command (rebuilds the cached directory index)
./sb resources status --remote scaleway-sandbox --refresh --json
# always-available: capacity plus the cached index, no disk walk
./sb resources status --remote scaleway-sandbox --fast
./sb resources monitor --remote scaleway-sandbox --scheduled --json
./sb resources plan --scope cache --thorough --budget 60 --json
./sb resources plan --scope stale --thorough --budget 90 --json
```

`resources monitor` performs a bounded cache-only pressure pass (900 seconds
by default) and records the result. `--scheduled` is a trigger label only;
`--dry-run` guarantees that automatic cleanup and reaping do not delete, while
still allowing the local monitor record and review-plan metadata to be written.
The monitor policy is resolved before any host-facing service is built, and
warning/normal runs exit 0 while critical, unknown, refused, or failed runs
exit 1. Automatic reclamation and real reaping are off by default.

Planning is read-only. Cleanup requires a current target-bound plan plus
`--confirm`, revalidates each exact candidate, and never uses a broad Docker
prune. Cache and stale persistent-resource cleanup are deliberately separate.
Deep status uses safe mount topology and opaque capacity-scope identities to
measure selected root, Sandbox, Docker, and typed managed filesystems once.
It uses installed `gdu` with allocated-block `du` fallback, deleted-open
allocated-block evidence, and Docker unique/shared/activity/reclaimable
diagnostics without double counting them. It is bounded (budget plus five
seconds), preserves valid partial/cancelled evidence, installs nothing, and
adds no cleanup path.

Deployment storage has its own tiered path. `status` classifies every entry of
`deploy-src` as PROTECTED / LIVE / STOPPED / REGONLY / BASE / ORPHAN with sizes,
mtimes, per-class totals, and index-versus-disk drift; `plan` previews a tier
with a reason per candidate and a skipped list; `cleanup` executes it, writing a
deletion manifest before each removal so "what happened to X" stays answerable:

```sh
./sb resources status  --remote scaleway-sandbox --deep --budget 180
./sb resources plan    --remote scaleway-sandbox --tier safe
./sb resources cleanup --remote scaleway-sandbox --tier safe --confirm
./sb workspace release <name> --remote scaleway-sandbox    # done with it
./sb workspace ttl <name> --ttl 14d --remote scaleway-sandbox
./sb workspace reap --remote scaleway-sandbox --dry-run
```

Only workspace-scoped `node_modules`-style volumes are ever eligible — every
other volume is protected at every tier, including ones the engine reports as
unused — hosted sites are untouchable, a partial delete is reported as a
failure rather than success, and the default retention window is 7 days.
See [Resource Monitoring and Safe Cleanup](docs/resource-monitoring.md).

### Durable remote-first jobs

When `sandbox.config.json` configures a provisioned `runtime.default: "remote"`,
Sandbox recommends remote execution. Local execution remains available only by
an explicit `--local` override. Remote job submission deploys the exact working
tree first, including uncommitted and untracked files; the remote supervisor
persists process output and callers resume it by cursor rather than streaming
child pipes over SSH.

`job-output` transfers only bounded pages from those retained logs. Select a
stream, tail, cursor, or bounded long-poll interval (0-20 whole seconds; zero
disables a one-shot wait) to suit the agent's output verbosity; `--follow`
converts a validated zero into its one-second polling wait. The complete sealed
log remains available for later retrieval.

Generic Compose `exec` failures retain stdout and stderr independently, each
bounded to the 1 MiB process-runner limit; when a stream overflows, the runner
keeps both edges around an explicit truncation marker. Failures include the child `exit_code`. Human `sb exec`
writes those streams to their matching local streams; `--json` returns one
envelope containing both fields and exits with the child code. A nested remote
controller uses the human path so the outer durable supervisor persists the
same separate evidence.

```sh
./sb exec --remote scaleway-sandbox --workspace node-unit --timeout 3600 --detach \
  --request-id node-unit-tests-1 -- npm test
./sb job-status <job-id> --json
./sb job-output <job-id> --follow
./sb job-output <job-id> --stream stderr --tail-bytes 8192 --wait-seconds 2
./sb workspace create --local --workspace node-unit
./sb workspace list --remote scaleway-sandbox --project-identity <id> --json
./sb workspace migrate --remote scaleway-sandbox --project-identity <id> --json
# Apply only the exact unexpired metadata-only plan after reviewing all records:
./sb workspace migrate --remote scaleway-sandbox --plan-id <plan-id> --confirm --json
./sb remote docker-pool scaleway-sandbox --json             # read-only plan
./sb remote docker-pool scaleway-sandbox --confirm --json   # backup, validate, restart, verify
./sb remote docker-pool scaleway-sandbox --recover-interrupted --expected-running 72 --json # evidence-bound recovery plan
# Plans include measured total/allocated/usable subnet fields; partial IPAM is null, never guessed.
./sb remote domains scaleway-sandbox --json                 # secret-free instance/host route inventory
./sb test matrix --local --workspace node-20 --workspace node-22 --timeout 3600 -- npm test
./sb test matrix --remote scaleway-sandbox --plan verify --timeout 1800 --json
./sb ci run .github/workflows/tests.yml --remote scaleway-sandbox --timeout 3600 --json
./sb job-artifact-get <child-job-id> <artifact-id> --remote scaleway-sandbox \
  --output-file tmp/report.tar
```

Workspace control is backed by an owner-only durable index under
`$SANDBOX_HOME/runtime/workspaces/index.sqlite3`. Remote list/status use project or
workspace identity rather than a deployed checkout path. Legacy `workspace.json`
files remain byte-preserved; ambiguous, malformed, or unattributed records are reported
as `workspace_index_incomplete` instead of an empty inventory. Migration is metadata-only
and never resets/destroys a workspace or removes a Docker network.

`workspace list` is a read-only report and stays successful when the index is degraded:
the payload carries `index.complete=false` with `index.code="workspace_index_incomplete"`
(mirrored as a top-level `code`/`warning`, and as a `WARNING:` line in text output), plus
an `on_disk` block enumerating every directory under the deployment root
(`$SANDBOX_HOME/deploy-src`) with `path`, `indexed`, `workspace_id`, `modified_at`,
`age_seconds`, and `size_bytes`. Sizes are `null` with a `size_reason` unless
`--measure-sizes` is passed, and even then the walk is bounded by entry and time budgets
(`size_budget_exhausted` / `size_deadline_exceeded` rather than a hanging `du`). This
keeps unindexed deployment storage visible for reclaim decisions. Degradation is not
weakened anywhere else: `workspace status`, create, reset, destroy, and migration apply
still refuse a degraded or non-ready record.

Remote CI is a durable parent/child submission. Sandbox preflights the workflow
and blocks named incompatibilities until explicitly accepted, deploys the exact
working tree once, then creates one isolated retained-log child per selected job
and matrix cell. Inspect `parent_job_id` and each child with `job-status` and
`job-output`; the submitting SSH/MCP connection never owns the workflow pipes.
The co-located `act` adapter runs on the remote host, which must advertise
`job.exec` and have any workflow-specific credentials configured there. The
remote provisioner installs `act`; GitHub's `actions/upload-artifact` is
converted to Sandbox's retained job-artifact collection because a self-hosted
`act` runner has no GitHub Actions runtime token. Remote CI preflight accepts only literal
project-relative upload paths with `if-no-files-found: error`; globs, expressions, and
unsupported upload options produce named blocking differences before execution. Literal artifact directories are
stored as deterministic bounded tar archives. CLI `--output-file` retrieval reads
all bounded pages into a temporary file, validates declared size and SHA-256, then
atomically publishes it; MCP artifact reads remain one bounded page per call.
Parent status preserves `aggregate`, frozen original `children`, and `result_json` while
adding a normalized terminal `result` capped at 256 KiB. Persisted child references carry
outcome, output completeness, artifact/difference counts, and cleanup state; full current
detail remains in `children`, and linked retries appear separately in `retry_attempts`.
Aggregate-parent retry returns `aggregate_retry_unsupported`; child retry reuses the
durable bounded submission snapshot without mutating prior terminal attempts.

Generic Compose instances have enforced default limits of 2 CPUs, 4 GiB RAM,
and 512 PIDs. The remote durable scheduler admits at most two jobs and checks
free memory/disk before starting another. Retrieve the authenticated, log-free
control-plane host snapshot with
`./sb remote service diagnostics <remote> --json`.
Add `--processes` for an opt-in, service-backed read-only snapshot grouped by the sanitized
`comm` name. It reports bounded process and optional Docker rows without command
lines, arguments, environment, paths, or sudo. CPU is the `ps` lifetime average;
RSS can double-count shared pages, host and container rows overlap, and the
point-in-time views can drift immediately. `comm` grouping is heuristic and its
CPU sum can exceed 100% on multicore hosts. This requires diagnostics schema 2;
update an older installed remote through the supported Sandbox lifecycle first.

Open the local dashboard with `./sb web`, then choose a configured remote from the
**Remotes** rail. Its inventory page shows hosted-instance counts, running/stopped
state, per-instance container memory/CPU attribution, process/apps, containers, jobs,
RAM/load/disk, and storage evidence. The quick view is cache-only and may be partial;
**Rebuild attribution** performs a bounded deep refresh through the authenticated
service. Unknown and overlapping values are shown as unknown/non-additive rather than
treated as safe cleanup candidates.

The dashboard uses a loopback HTTP BFF with one in-flight refresh per resource and
completion-based polling (30 seconds after the previous refresh finishes). Remote
summaries load independently from the slower local-instance status probes, so a
configured remote appears in the host rail promptly; its full inventory remains lazy
and bounded. It does not open a WebSocket: the expensive operation is the host
inventory itself, so a push channel would not make that scan cheaper. A future event
stream can be added behind the same single-flight cache if remote services begin
emitting incremental changes.

For the exceptional case where an operator must run a command directly on a host,
use the explicit CLI escape hatch. It is never used internally and is not exposed as
an MCP tool:

```sh
./sb remote ssh <remote> --confirm --reason "diagnose service" --command 'systemctl --user status sandbox-remote-mcp'
```
Normal diagnostics, resource probes, dashboard inventory, and future service-backed
operations never fall back to it.

Projects whose service startup bootstraps dependencies can declare a bounded
`compose.startupTimeoutSeconds`; persistent workspaces can additionally opt
into `compose.recreateOnEnsure` to rerun that bootstrap after each deployed
source revision while retaining named volumes.
If the health deadline expires, the durable result includes a bounded tail of
the declared service's Compose logs for diagnosis.

Use the same runtime operations without an MCP client:

```bash
./sb guide --project-dir .        # runtime-aware command catalog
./sb skill show sandbox-cli       # CLI-first operating skill
./sb ensure                       # start/reconcile local instance
./sb ensure --json --reveal-login # ...and emit a usable admin autologin URL
./sb exec -- sh -lc 'npm test'    # generic Compose projects only
./sb exec --project-dir <dir> -- sh -lc 'npm test'  # select project from any cwd
./sb deploy --remote <name> --ensure --expose
```

`--json` output is redacted: every credential-shaped field, including the
`sandbox_autologin` token inside `login_url`, comes back as `[REDACTED]`. Test
harnesses that need to open an admin session without a password pass
`--reveal-login`, which restores `login_url` alone (other credentials stay
redacted). A local instance qualifies when its host is loopback-bound; a remote
ensure record qualifies on the flag, which is forwarded to the VPS so its own
redaction runs after. A revealed URL for a publicly exposed instance is an
admin credential — keep it in a gitignored descriptor, out of logs and commits.

If `login_url` carries a `sandbox_autologin` parameter, the same JSON document
also contains the derived `login_url_redacted` boolean. It remains `true` for
placeholders, already-redacted input, unusable/non-loopback local URLs, and
failed reveals; only a successful explicit `--reveal-login` produces `false`.
It derives from a boolean-only classification of raw input before redaction;
any producer-supplied status is discarded. That classification never emits the
raw URL or token; a validated URL is emitted only by explicit `--reveal-login`.
Remote reachability checks are read-only and strict: one non-multiplexed
`ssh ... true` probe, bounded to a 15-second connect timeout and 20-second
overall timeout, with no stateful transport fallback. `./sb remote list --json`
also reports a safe reachability state and measured latency so a timeout is not
collapsed into a generic unreachable result.

`./sb mcp --project-dir .` remains available for an MCP-capable client. It is
runtime-scoped: generic Compose projects do not load WordPress tools, and
WordPress projects do not load generic container-exec tools.

## What Claude can do — the MCP tools

After `setup`, the single `sandbox` server exposes these against the live stack.
**Every tool takes `project_dir`** (the agent passes your plugin's root, or cwd)
and resolves the target instance from the registry — booting one if needed.

| Tool | Purpose |
|------|---------|
| `ensure_instance` | Boot (create-if-missing) the instance for a project dir; a ready Docker instance attests source mounts and verifies WordPress install state before returning. Mount drift/refusal returns `instance_mount_drift` or `instance_mount_state_unavailable`; an indeterminate install probe returns `instance_install_state_unavailable` with `mutated:false` and no writes. |
| `destroy_instance` | Permanently delete an instance (containers, DB volume, wp dir, registry) |
| `recreate_instance` | Destroy then immediately recreate — clean WP install from current config |
| `run_tests` | Run the plugin's phpunit tests on the external WP harness → pass/fail + failures |
| `run_plugin_check` | Run WordPress.org's Plugin Check, gated by a committed baseline → pass/fail + new findings (see `docs/plugin-check.md`) |
| `remote_deploy` | One-way, on-demand push of local project state to a registered remote VPS (see `docs/remote-hosting.md`) |
| `wp_cli` | Run any `wp` command |
| `wp_exec` | Arbitrary shell in any container (composer, npm, php, …) |
| `wp_rest` | Call the WordPress REST API (pre-wired app password) |
| `http_fetch` | Lightweight anonymous HTTP probe — status, headers, body, redirects |
| `visit` | Headless Chromium; auto-logs in on `/wp-admin/`. Returns status + DOM + iframes + console + network + optional screenshot |
| `db_query` | Run SQL — writes require `mutate: true` |
| `snapshot` / `wp_reset` | Capture a named snapshot (`db_only: true` skips uploads) / reset to protected `@install` (`confirm: true`) |
| `tail_log` | Tail `wp-content/debug.log` |
| `fs_read` / `fs_write` / `fs_list` | Read/write files under the instance's WP dir |
| `mail_list` / `mail_get` | Read Mailpit (test SMTP inbox) |
| `focus_get` | The project's focused plugin and available skills; pass `include_claude_md=true` when the project guide is needed |
| `activate_plugin` / `deactivate_plugin` | Toggle plugins |
| `import_content` | Import a WXR XML from `runtime/seeds/` |
| `load_context` | Pull the full sandbox `CLAUDE.md` on demand |
| `load_skill` | Pull a skill (`fix`, `bug-repro`, `snapshot`, `wp-debug`, `wp-pilot`, `fluentboards`) |
| `load_workflow` | Pull a workflow (`build-feature`) |
| `feedback_submit` / `feedback_list` | Send or inspect bounded, secret-redacted agent feedback stored as untrusted machine-local data (see `docs/feedback.md`) |

Plus Claude's normal `Read`/`Write`/`Edit` reach the plugin source on disk —
bind-mounted into the container, so edits are live with no rebuild.

You can also invoke skills as slash commands, e.g.
`/mcp__sandbox__activate` (load the full operating guide) or
`/mcp__sandbox__fix <task>` (one-pass bug-fix loop).

---

## Managing instances

Instances are created per-project by `init`/`ensure`; the browser dashboard also offers
a local **Create an instance** form backed by the same `ensure` operation. Remote
creation remains unavailable until the remote lifecycle service exposes it. You can
view and drive instances with:

```bash
./sb instances            # list every per-project instance + status + URL
./sb instance list        # discoverable singular alias for the same inventory
./sb dashboard            # full-screen TUI: start/stop/restart/open/focus/delete
./sb web                  # the same dashboard in the browser (127.0.0.1:8765)
./sb instance suspend <name>  # graceful stop; idle_stop is the resolved default
./sb instance resume <name>   # start a provisioned instance and wait for readiness
./sb instance delete <name>   # tear one down (containers, volume, files, registry)

# Remote inventory and exact-name teardown (never glob remote runtime paths):
./sb instances --remote <name> --json
./sb instance delete <exact-instance-name> --remote <name> --yes
```

The macOS desktop shell reuses this loopback dashboard and adds a narrowly scoped
native project-folder picker. It never exposes Docker, SSH, credentials, or generic
filesystem/process access to the renderer; see [docs/desktop-app.md](docs/desktop-app.md).

Request wake defaults on for newly resolved Docker instances. Set
`instanceLifecycle.mode: "always_on"` to opt out and pin an instance on.
Clean-URL setup installs and enables the per-user activation service before
eligible Caddy routes authorize readiness through stock `forward_auth`. The
same service scans idle routes and gracefully suspends them only after live
runtime, established HTTP/WebSocket connection, durable-job, and WP-CLI lease
checks pass. Missing evidence pins the route. Invalid catalogs or an unhealthy
authority retain direct/previous Caddy routes. Existing registry rows adopt the
default only on normal `ensure`/`apply`. Remote wake and function/FaaS adapters
remain future work. The supervised authority refreshes its catalog from the
current registry/config on authenticated activation requests and scheduler
cycles, so it does not keep serving a stale route snapshot after an instance
is created, changed, or removed. Liveness stays independent of registry I/O;
a failed refresh revokes the previous route allowlist until the source is
readable again.

```bash
./sb activation status
./sb activation status --json
./sb activation scan --dry-run
./sb activation enable
./sb activation disable
```

`activation enable` treats a healthy loopback authority as success even when a
macOS service-manager transition reports a transient non-zero result; the JSON
response may include a warning while the supervisor is already serving.

Each instance can run a different **web server**, and you can switch in place
without re-importing content:

```bash
./sb server <name> nginx        # apache → nginx (adds the nginx sidecar)
./sb server <name> litespeed    # → OpenLiteSpeed
./sb server <name> apache       # → back to apache
```

### Clean URLs — Docker/Caddy by default

The default provider is Sandbox's own Caddy proxy plus Sandbox-owned DNS, on every
platform and for every runtime. One optional setup upgrades every instance to a
trusted, no-port URL:

```bash
./sb domains setup            # default provider: Caddy proxy + *.tst resolution
./sb domains use              # show the active provider
./sb domains use herd-valet   # opt in to a host incumbent instead (switchable anytime)
```

Host-incumbent adoption is opt-in and has its own read-only planning surface:

```bash
./sb domains plan --project-dir .
./sb domains apply --project-dir .   # first mutation asks only in a terminal
./sb domains cleanup --project-dir . # compare-before-remove; safe to retry
```

Adapter proof tiers gate adoption only, never the default path. The instance stays
usable at `http://localhost:<port>` when the selected provider is unavailable. See
[the clean-URL default](docs/clean-url-default.md) and
[domain resolution](docs/domain-resolution.md).

---

## Daily commands

```bash
sandbox init              # in a plugin dir: config + instance + test harness
sandbox ensure            # boot/refresh this project's instance
sandbox apply             # reconcile THIS project in place (cwd or --instance)
sandbox test [-- <args>]  # run the plugin's phpunit tests (pass extra phpunit args after --)
./sb focus <plugin>       # mark which plugin is focused (for Claude)
./sb open [admin|site|mail]  # open in browser (default: admin)
./sb visit <url> [...]    # load any URL in headless Chromium, report DOM/console/iframes
./sb snapshot <name> [--db-only]  # save DB + uploads, or fast DB-only state
./sb restore <name>       # restore a saved snapshot
./sb reset --yes          # restore the protected post-install DB baseline
./sb update               # git pull the project repo this instance tracks
./sb xdebug on|off        # toggle step-debug (port 9003, host trigger)
./sb zip [--dev|--clean]  # build the distributable plugin zip (see docs/plugin-zip.md)
./sb doctor [--instance NAME|--label LABEL] [--json]  # audit one local instance + controller health
./sb status               # which containers + project + focus are active
./sb status --instance <name> --json  # inspect a known local instance from any cwd
./sb status --project-dir <dir> --json  # inspect a registered local project from any cwd
./sb status --remote <name> --instance <remote-instance> --json  # inspect a known remote instance directly
./sb down                 # stop containers (state preserved)
./sb clean                # stop + wipe DB volume (start fresh)
```

Run `./sb` with no args for the full list. `doctor` runs on the local controller and
intentionally has no `--project-dir`, `--local`, or `--remote`; run it from the project
directory, or resolve the registered instance with `./sb instances --project-dir DIR --json`
and pass `--instance NAME`. Most instance-scoped commands accept
`--instance <name>`; project-routed `ensure`/`test`/`init` use
`--project-dir <dir>` (and `--label` where supported). Use
`sb apply --instance NAME` to reconcile an existing named instance.

`sandbox test` / `./sb test` dispatches plugin test modes: `auto` resolves to
`unit` or `integration`; `integration` provisions and runs the external
WordPress/PHPUnit harness, while `unit` runs plugin unit PHPUnit with the runner tools. Declared Compose modes and
`matrix` are separate execution paths; none run Sandbox's own Python tests. To test
this checkout, use the stdlib `unittest` commands in
[`tests/README.md`](tests/README.md), for example:

```bash
.cli-venv/bin/python -m unittest tests.test_cli.TestResolutionGate -v
.cli-venv/bin/python -m unittest discover -s tests -p 'test_feedback.py' -v
```

---

## Configuration

Two layers:

- **Per-project** `sandbox.config.json` (in the plugin repo, canonical) +
  gitignored `sandbox.config.override.json`. This is what makes a plugin a
  sandbox project. See [`docs/sandbox-config-reference.md`](docs/sandbox-config-reference.md).
- **Machine/global** [`sandbox.yml`](sandbox.yml) — ports base, admin creds,
  image defaults. Per-machine overrides go in the gitignored `sandbox.local.yml`:

```yaml
defaults:
  plugins_home: "$HOME/dev"     # where cloned plugins live
  pro_plugins_home: "$HOME/Sites/plugins-pro"   # shared Pro store, offered on demand
  github_org: "wpdeveloper"
```

`pro_plugins_home` (default `~/Sites/plugins-pro`) is the one directory holding Pro
plugin copies. `./sb deploy` and `./sb remote plugins <name>` mirror it to a remote
host so every instance there lists the same slugs on **Plugins → Sandbox On-Demand**
— see [`docs/remote-hosting.md`](docs/remote-hosting.md).

There is **no central project catalog** — each plugin self-describes.

### Clean URLs and host ingress

Host ingress adoption is the opt-in alternative to the default Docker/Caddy provider
(`./sb domains use <provider>`); see [the clean-URL default](docs/clean-url-default.md).

`./sb domains ingress support --json` lists host products and their current proof tier;
`detect`, `status`, and `plan` are read-only. A product being detected does not mean Sandbox
may alter it: only an adapter with a documented control surface and accepted live proof can
become adoptable. A machine-local ingress override, when configured, beats a committed
project pin; an unavailable explicit pin returns the per-port URL rather than selecting a
different host service.

Route adoption requires a verified DNS handoff, interactive consent on first use, and an
owned route record. `cleanup` and `reconcile` remove only unchanged owned routes; drift or
an unavailable incumbent retains non-secret recovery state. In CI/MCP, pending consent or
credentials returns immediately and never prompts. See [host ingress adoption](docs/host-ingress.md)
and the [configuration reference](docs/sandbox-config-reference.md#host-ingress-and-clean-urls).

The current mutation surface is deliberately narrow: Linux system Caddy, exact HTTP
hostnames, an already-enabled `/etc/caddy/conf.d/*.caddy` import, and an explicitly
installed owner-scoped helper. HTTPS, wildcard routing, and every other incumbent remain
unadvertised. The helper installation and pending live-evidence gate are documented in the
host-ingress guide.

---

## Bringing your own CLAUDE.md and skills

Three attach points, all automatic:

1. **Sandbox `CLAUDE.md`** — the operating guide, loaded on demand via
   `load_context` (the compact summary ships every session via the MCP
   `instructions` field).
2. **Project `CLAUDE.md`** — a plugin repo's own `CLAUDE.md` (+ any
   `.claude/skills/<area>/SKILL.md`) is surfaced by `focus_get` for that project.
3. **Personal skills** — `~/.claude/skills/*/SKILL.md` are loaded by Claude Code
   itself, alongside the sandbox.

**Skills** (loaded via `load_skill('<name>')`): `fix`, `bug-repro`, `snapshot`,
`wp-debug`, `wp-pilot`, `fluentboards`. **Workflows** (`load_workflow('<name>')`):
`build-feature`. Each lives in its own folder with an uppercase entry file
(`skills/<name>/SKILL.md`, `workflows/<name>/WORKFLOW.md`).

---

## What lives where

```
sandbox/
├── sb                      # the CLI (Python — invoke as ./sb or `sandbox`)
├── sandbox_core.py         # shared core: per-project config + registry
├── sandbox.yml             # machine/global defaults
├── sandbox.local.yml       # per-machine overrides (gitignored)
├── bin/sandbox.js          # npm entry shim (execs the bundled sb)
├── package.json            # npm package (@alimuzzaman/sandbox)
├── packaging/              # Homebrew formula + packaging notes
├── docker-compose.yml      # managed by the CLI
├── runtime/
│   ├── wp-<instance>/      # each instance's WordPress install (bind-mounted)
│   ├── registry.json       # project-root → instance mapping
│   ├── test-suite/         # cached wordpress-develop phpunit suite
│   ├── test-tools/         # phpunit + composer phars + polyfills + wp-tests-config
│   └── seeds/              # demo content / WXR imports
├── plugins/                # default home for cloned plugin repos (gitignored)
├── mcp/wp-server/          # the Python MCP server + its venv
├── skills/<name>/SKILL.md  # role packs
└── workflows/<name>/WORKFLOW.md
```

The only state outside this folder: Docker's named volumes (cleared by
`./sb clean` / `./sb instance delete`).

---

## Troubleshooting

```bash
./sb doctor       # checks containers, WP, REST auth, MCP venv, symlinks, project, focus
```

- **REST auth fails** — re-run `./sb ensure` (regenerates the app password).
- **MCP server not connected** — `claude mcp list` should show `sandbox` as
  `✓ Connected`. If missing, re-run `./sb setup`. For the project-local
  fallback, `cat .mcp.json` (it points at `./sb mcp`).
- **A plugin "isn't found"** — make sure you've run `sandbox init` (or `ensure`)
  in its directory so it has a `sandbox.config.json` + a registered instance.
- **Container won't start** — `./sb ensure` resumes a stopped/half-booted
  instance in place; if Docker itself restarted (e.g. an auto-update), relaunch
  Docker and re-run `ensure`.
- **Fresh start** — `./sb instance delete <name>` then `sandbox init` again.

For everything else, ask Claude — it has `tail_log`, `wp_exec`, and `db_query`
and can usually diagnose itself.

---

## Roadmap

- **Shipped** — Docker WP stack; the single `sandbox` MCP server routing by
  `project_dir`; per-project `sandbox.config.*` + on-disk registry;
  externally-provisioned phpunit harness (`sandbox test` / `run_tests`);
  `sandbox init`; server-aware version pins; headless Chromium with auto-login
  (`visit`); size-scaled `build-feature` workflow; one-pass `fix` skill;
  FluentBoards integration; Plugin Check; first-pass remote VPS hosting; managed
  Compose-host validation and confirmation-gated permanent Cloudflare DNS/TLS deployment;
  personal `~/.zshrc.secrets` support; npm +
  Homebrew + curl distribution.
- **Next** — protected recovery and Hermes/Lenzora acceptance remain operator-gated.
  Use the consolidated [release-readiness checklist](docs/release-readiness.md)
  before a release, then see [`docs/future-roadmap.md`](docs/future-roadmap.md)
  for deferred product work.

Re-run `./sb setup` after a global config change — it's idempotent.
## Hermes Agent

Remote Hermes control is documented in [docs/hermes-agent.md](docs/hermes-agent.md).
Its optional public dashboard route uses Cloudflare Access and Tunnel while keeping
Hermes loopback-only; see the public-route section in that guide before any live apply.
Fresh `sb hermes setup` also prepares the Spark/Luna/Terra/Sol routed-worker profile;
provider authentication and gateway activation remain explicit operator steps.
Hermes scheduled state is reproducible from the committed cron catalog: use
`sb hermes cron reconcile --remote NAME` to preview, then repeat with
`--confirm --force-replace`. `sb hermes health` reports false-green provider
errors, catalog drift, competing gateway owners, and dirty managed worktrees.
