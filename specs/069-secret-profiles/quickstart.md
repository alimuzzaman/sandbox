# Quickstart / validation guide: 069 Secret Profiles

Synthetic data only. No real secret file, credential, env file or `secrets.asb.bd` tenant. Formal tests run **after all implementation**, by Haiku 5.5 agents, through `tests/subprocess_support.synthetic_environment` / `run_test_process`; each report states command and outcome.

## 1. CLI synthetic acceptance (first)
`tests/test_secret_ownership_cli_acceptance.py`, real `./sb` CLI in a synthetic `SANDBOX_HOME` and project with a dotenv fixture (`ALPHA`, `BETA`, `GAMMA` synthetic values):
1. No `ownership` block → `sb secrets inspect|run|use-profile|organize|migrate-zshrc` output and `a1-` digests identical to baseline (FR-051, SC-008).
2. `classify --profile personal` with `GAMMA` undecided and `BETA` twice → exit 2; names listed under `undecided`/`multiply_assigned`; existence `unchecked`; source bytes and stat unchanged (US1).
3. `approve` with stdin not a TTY → `approval_human_only`; via MCP surface → refused (FR-032).
4. `migrate --confirm` with no approval → `approval_required`; with evidence missing/expired/version-only → `evidence_*`; an injected test-only unconfirmed manifest → `provider_contract_unconfirmed`; 0 provider contact.
5. Value-only edit of `ALPHA` with `os.utime` restoring mtime after approval → `source_changed` (ctime advanced) (FR-035).
6. `run --ownership-profile personal` without keys → `ownership_keys_required`; with keys from both domains → `other_domain`; name-only child probe (`env` names) shows exactly the subset (SC-007).
7. Leak scan of stdout/stderr/journal/approval/run records for every synthetic value, forbidden value-derived fields and `r1_` without computing a value hash → 0 hits (SC-001).

## 2. In-process provider-path acceptance (fake adapter)
`cmd_secrets(cfg, args, ownership_adapter=FakeOwnershipAdapter(script))`: clean run → all `verified`; initial pre-existing → 0 writes (SC-002); conflicting insert after preflight → stop, item untouched, prior receipts kept (SC-005); interruption after `attempted` → `acceptance_unknown`, resume reconciles to exactly one item (SC-003); token expiry → stop, resume needs re-approval, wider authority refused, 0 repeated writes (SC-004); `{"approval":…}` and malformed bodies → 0 created/verified, 0 cleanup-eligible (SC-006); revocation failure → `cleanup_unknown` + next action, exit 6.

## 3. Focused regressions
`test_secret_ownership_{config,classify,continuity,worker,journal,approval,migrate,cleanup,scoped_run,boundaries}.py`, then existing `tests/test_secret_*.py` and `tests/test_mcp.py`. Boundary test: AST check that `sandbox/secrets/ownership/*` never imports `opaque_revision`, `load_revision_key`, `sandbox_core`, `sandbox.registry`, `sandbox.hermes`, MCP `app`.

## 4. Disposable-instance live proof (human-gated, not claimed)
Separate disposable Infisical instance (never `secrets.asb.bd`), provisioned under its own human approval with synthetic project/identities. Prove R4 (hidden-value shape, 404), R5 (400 duplicate, approval body under a policy), R6 (V2 selection query, index state), R7 (revoke 200). Until each is run and recorded, mutation against any real destination stays blocked at the human security review / restricted-runtime provisioning gate.

Execution uses standard-library `unittest` and Python 3.12+ `--durations 0`; no pytest dependency is required. The acceptance run contains individually timed expanded cases. Use `run_test_process` for the captured suite runner as well as captured children inside tests.
