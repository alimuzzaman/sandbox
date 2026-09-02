# Quickstart: Validate Instance-Scoped Server Configuration Fragments

This is the release-evidence guide, not implementation code. Use disposable local
instances and non-secret cache fixtures. Do not use raw Docker, SSH, direct runtime-file
edits, or retained-state paths. A local unit/fake-runner pass is not live proof.

## 1. Preconditions and exact revisions

Record:

```bash
git rev-parse HEAD
git status --short --branch
./sb guide --project-dir <nginx-target-project> --json
```

Required before acceptance:

- branch is clean and contains Feature 047/048 integration;
- installed `sb` revision matches the tested Git/runtime revision mapping;
- four disposable projects are registered: nginx target/control and OpenLiteSpeed
  target/control;
- every project has its own instance incarnation and clean `.tst`/supported URL;
- target and control instances are ready before the first mutation;
- xSpeed or the chosen fixture plugin can emit server-type-specific
  `wordpress-cache-v1` fragments, warm/purge a test route, and expose a request-scoped
  PHP execution sentinel;
- fragments and evidence contain no credentials, cookies, login URLs, or production data.

If an existing instance predates the server-config mount, reconcile it through the
supported instance path and recheck readiness:

```bash
./sb apply --instance <instance>
./sb status --instance <instance> --json
```

`server config apply` must refuse an unattached legacy runtime rather than silently
recreating it.

## 2. Focused local gates

Run the feature and compatibility suites with a synthetic test environment:

```bash
python3 -m unittest \
  tests.test_server_config_models \
  tests.test_server_config_policy \
  tests.test_server_config_repository \
  tests.test_server_config_service \
  tests.test_server_config_nginx \
  tests.test_server_config_openlitespeed \
  tests.test_server_config_cli \
  tests.test_server_config_lifecycle \
  tests.test_server_config_isolation \
  tests.test_cli \
  tests.test_modularity \
  tests.test_lifecycle
```

Required focused cases:

- exact size boundaries, stable regular-file/stdin reads, symlink/special-file refusal;
- secret-like/invalid names and content-free JSON/errors/logs;
- deny-by-default nginx/OLS grammar including out-of-authority protected routes;
- complete-set conflict and deterministic order;
- identical apply and absent revert no-ops with zero validator/reload calls;
- atomic state, lock contention, crash after every phase boundary, corruption, drift,
  and deletion/name reuse;
- exact-image argv selection, OLS network-none/data-free validator, target-only calls;
- injected post-validation activation and readiness failures proving exact rollback,
  one recovery activation maximum, and truthful recovery-needed timeout;
- all current legacy `sb server` switch forms;
- `list`/metadata `show` pre-dispatch causes zero persistent writes;
- Feature 047/048 command/hosting/recovery tests after rebase.

Use injected adapter faults for rollback tests. Invalid syntax is a separate
pre-activation refusal and must not be mislabeled as rollback.

## 3. Capture the control baseline

For each target/control pair, retain content-free JSON from supported commands:

```bash
./sb status --instance <target> --json
./sb status --instance <control> --json
./sb server config list --instance <target> --json
./sb server config list --instance <control> --json
```

Record target/control incarnation, runtime/image identity projection, fragment-set and
effective-generation identity, readiness, and response marker. The control set must be
empty or separately known and remain byte-identical in evidence through every target
operation.

## 4. nginx live acceptance

Start with a ready nginx target and control. Obtain the plugin-emitted nginx fragment in
a caller-owned temporary file through the plugin's supported `sb wp` command or fixture;
do not hand-author the production acceptance fragment.

```bash
./sb server config apply --instance <nginx-target> \
  --name xspeed-static-cache \
  --authority wordpress-cache-v1 \
  --file <caller-temp-nginx-fragment> --json

./sb server config list --instance <nginx-target> --json
./sb server config show --instance <nginx-target> \
  --name xspeed-static-cache --json
```

Expected:

- apply reports `active`, successful validation/reload/readiness, and no content;
- list contains exactly one name/content ID and a healthy set identity;
- metadata show matches it and performs zero writes;
- control baseline remains unchanged.

Warm the selected public test route through the plugin's supported `sb wp` operation.
Use the checked-in acceptance HTTP-probe fixture through `sb wp eval-file` (or the
Sandbox `http_fetch` tool) to capture only response status, the expected server hit
header, request ID, and PHP sentinel before/after. Do not use raw `curl`.

Required proof:

- warmed response includes `X-XSpeed-Cache: HIT (nginx)`;
- the matching request-scoped PHP sentinel does not advance;
- response body/source inspection is not used as a substitute;
- control response marker, sentinel, set/runtime identities, and readiness are unchanged.

Then revert:

```bash
./sb server config revert --instance <nginx-target> \
  --name xspeed-static-cache --json
./sb server config list --instance <nginx-target> --json
```

Probe the same route again. The fragment must be absent, nginx ready, the server hit
marker absent, and the request-scoped PHP sentinel must advance.

## 5. OpenLiteSpeed live acceptance

Switch/create only the disposable target and control through supported commands and prove
both are ready on canonical server type `litespeed`. Obtain the plugin-emitted OLS cache
fragment through its supported `sb wp`/fixture path.

```bash
./sb server config apply --instance <ols-target> \
  --name xspeed-lscache \
  --authority wordpress-cache-v1 \
  --file <caller-temp-ols-fragment> --json

./sb server config list --instance <ols-target> --json
./sb server config show --instance <ols-target> \
  --name xspeed-lscache --json
```

Apply evidence must identify the exact active OpenLiteSpeed image and successful isolated
validation by evidence digest only. It must not contain validation-container metadata,
native logs, content, or paths.

Using plugin-owned warm/purge commands through `sb wp` and the same supported HTTP-probe
fixture/tool, record this exact sequence:

1. origin response with PHP sentinel advance;
2. warm action;
3. server-cache hit marker with no PHP sentinel advance;
4. plugin purge action;
5. non-hit/origin response with PHP sentinel advance;
6. rewarm action;
7. server-cache hit marker with no PHP sentinel advance;
8. `server config revert`;
9. origin response with PHP sentinel advance.

After every target mutation, recapture the control set/runtime/marker/readiness and prove
all remain unchanged.

## 6. Refusal and idempotency matrix

On each server type, capture target/control state before and after:

```bash
./sb server config apply --instance <target> \
  --name invalid-fragment --file <invalid-syntax-file> --json

./sb server config apply --instance <target> \
  --name unsafe-fragment --file <out-of-authority-file> --json
```

Both must return `refused`, `mutated:false`, zero reload, unchanged set identity, and ready
target/control. Also verify wrong-server input, empty/oversized input, symlink/special
source, invalid/credential-like name, unstable read, conflicting stdin/file, and protected
route attempts.

Apply a valid fragment twice under the same name. The second result must be `no_op` with
zero validation boot/reload and one active record. Apply different valid content under
the name and prove one replacement record, then revert twice; the second healthy revert
must be `no_op`.

## 7. Automatic rollback evidence

Run the focused integration acceptance that wraps the real target adapter with the
checked-in test-only fault seam. It must allow exact-image validation, fail only after
candidate activation or during readiness, and interact with the runtime exclusively
through the same Sandbox gateway. No production command flag, environment escape hatch,
raw Docker call, or direct state edit may exist.

Capture:

- prior and candidate set/generation IDs;
- `rolled_back` terminal result and original failure phase;
- exactly one recovery activation;
- effective generation equals the exact prior generation;
- target readiness and unchanged control evidence.

Run the rollback-timeout variant and require `recovery_needed`, no success claim, and a
later apply refusal. This test does not replace nginx/OLS live behavior proof; both are
release gates.

## 8. Inspection and lifecycle gates

Verify:

- `list`/default `show` do not change any repository/Compose/environment timestamp or
  digest, including degraded/corrupt/stopped observations;
- `show --content` emits only exact bytes and refuses `--json`;
- `show --output` creates/replaces only a safe owner-only file and JSON stays content-free;
- server switch refuses before writes while a fragment or transaction is active;
- ordinary delete refuses active/unhealthy fragment state; exact confirmed delete removes
  the incarnation state;
- recreating the same display name yields a new incarnation and empty fragment list;
- stop reports `stopped`; start reconciles the committed generation before ready.

## 9. Done gate

Do not call the feature done until all are true:

- focused tests pass after Feature 047/048 rebase;
- public docs and Sandbox CLI skill match all contracts and error meanings;
- live nginx and OpenLiteSpeed target/control sequences pass on exact recorded images;
- invalid and out-of-authority input proves zero activation/reload;
- injected post-validation failure proves exact rollback and recovery-needed behavior;
- no other instance changes;
- no content/secrets/private paths appear in routine captures;
- exact Git/runtime revisions and evidence digests are retained;
- independent security/human review approves the consequential server-config path.
