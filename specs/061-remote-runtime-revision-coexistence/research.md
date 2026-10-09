# Research: Remote Runtime Revision Coexistence

## R1 — Where the protocol range is declared
- Decision: constants in `sandbox/remote_runtime/protocol.py`; migrate writes `Environment=SANDBOX_REMOTE_MCP_CONTROL_PROTOCOL=<spoken>:<oldest>` into the unit next to the revision line; the status probe reads it.
- Rationale: the module is under `sandbox/`, so the runtime revision digest covers it; the unit environment is already the probe's source of the installed revision, so no new remote file or round trip is needed.
- Alternatives: a file in the remote venv (needs another probe and can drift from the unit); asking `/mcp` (fails on degraded services, which must still be classified).

## R2 — Verdict rule
- Decision: `compatible` iff `installed.oldest <= local.spoken <= installed.spoken`; above → `protocol_newer`; below → `protocol_too_old`; undeclared installed → `exact_only` (compatible only on equal revision); status not determinate → `unknown`. Strict mode additionally requires equal revision and a registered pin.
- Rationale: monotone, two integers, matches the PRD.

## R3 — Pin storage and transport
- Decision: one JSON file per holder at `<remote SANDBOX_HOME>/runtime/remote-pins/<holder>.json`, written atomically under `remote-pins/.lock` (flock) by a fixed Python program sent over `ssh_run`, bounded at 15 s.
- Rationale: the deployment-receipt writer already uses this pattern; works on any runtime; flock makes register/break/release atomic against a concurrent migrate.
- Alternatives: a control-service endpoint (not available on old runtimes; see plan Complexity Tracking).

## R4 — Holder identity
- Decision: `h-` + first 16 hex of sha256(local Sandbox home realpath + "\0" + checkout realpath). Displayed with the checkout path.
- Rationale: stable per checkout per machine, secret-free, safe as a file name.

## R5 — Strict pin gate placement
- Decision: the verdict entry point (`require_compatible(remote, name, purpose)`) performs the strict check and pin register/renew, so every consumer gets strict behavior without its own code.
- Rationale: FR-003 forbids a second rule; one entry point is the only way to keep strict and compatible paths identical across consumers.

## R6 — Remedy validation
- Decision: remedies are built as argv lists from the remote name and rendered with `shlex.join`; a test parses every remedy builder's output with the real argparse parser.
- Rationale: FR-017; today's `--remote NAME` hint was rejected by the parser.

## R7 — Per-remote registration lock
- Decision: `registered_remote_lock(name=None)`. Named: shared flock on `registry.lock` plus exclusive flock on `remotes/<name>.lock`. Unnamed: exclusive flock on `registry.lock` (legacy, used only by callers without a name). Registry read-modify-write additionally holds a short exclusive `registry-write.lock`. The holder writes pid, command and start time into its lock file; a timeout reads it back.
- Rationale: different remotes never contend; a long apply on A blocks only A's registration changes; concurrent writers to different remotes cannot lose updates because the YAML rewrite is serialized and re-reads the block.
