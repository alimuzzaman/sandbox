# Reviewable future runtime acceptance plan

Status: NOT EXECUTED. This plan describes full US1–US6 acceptance, not checkpoint
completion or installed capability. It authorizes no runtime action. Current design
worker ran no tests/builds/apps/network/remote jobs or credential/link mutations.

## Preconditions and stop boundaries

Before execution the coordinator must record owner authorization for the named remote,
fixture stacks, installation/lifecycle action, link mint/revoke, fault injection and
teardown. Obtain exclusive host slot from the full-stack owner; do not touch shared
dev, setup-plan, fs-try or other people's links/keys. Reserve browser and edge-nginx,
hosting-caddy, docker-pool and per-target effect locks through supported ownership APIs.
Check active durable jobs and retain request/job ids; no competing apply/recovery.

Record local revision with `git rev-parse HEAD`. Read current command catalog via
`./sb guide --project-dir .`; obtain remote runtime revision with the documented
`./sb remote service status R --json` only after R is owner-authorized. This status
does not itself prove the four gate capabilities. Compare installed controller/gate
capabilities with the exact reviewed source revision using a supported observation
whose command is currently UNKNOWN. Use only the supported owner-approved lifecycle
update command, then observe installed revision independently. Do not equate source
revision 93431f7/e04e293, a source capability constant, or a successful upload with
installation. Require installed ALL FOUR capabilities `stack-page-v2`, `stack-signin-pool-v1`,
`wordpress-claim-v1`, `companion-status-v1` before key/grant effects; exercise old-capability
refusal with zero effects. Source capability response is not installed proof.
No raw SSH/Docker/curl edits or test-only protocol bypass.

Unresolved target sheet, MUST be filled and reviewed before corresponding steps:

| Placeholder | Required value/evidence |
|---|---|
| R, PARENT, CHILD | UNKNOWN until owner authorizes them: one registered disposable remote and two task-owned recorded fixture stacks; CHILD is an isolated Hub fixture, never shared dev |
| TEMPLATE_WP, TEMPLATE_HUB, REF | UNKNOWN until owner authorizes them: absolute disposable fixture templates, exact reviewed source ref, reserved `.test` hostnames and bounded windows; no real public hostnames |
| INSTALLED_STATUS, LIFECYCLE_UPDATE | Remote revision observation is `./sb remote service status R --json`; exact installed gate-capability observation and owner-approved lifecycle update remain UNKNOWN |
| JOB_STATUS, JOB_OUTPUT, LOCK_STATUS | Guide documents `./sb job-status JOB_ID` and `./sb job-output JOB_ID`; exact bounded-output flags, ownership evidence and lock-status command remain UNKNOWN |
| FAULT_CONTROL | UNKNOWN: supported isolated-fixture mechanism for lost mint reply, cleanup failure, gate restart and crash checkpoints; no invented CLI flag |
| ATTEMPT_LIST_REVOKE | Supported selectors: `./sb host share list --remote R --stack PARENT --json`; `./sb host share revoke PARENT_LINK_ID --remote R --stack PARENT --json`; stack-wide `revoke all` also accepts `--stack`. Installed behavior and authorization remain UNKNOWN |
| GATE_CALLBACK_WIRE | Source wire fixed in contracts/access.md; UNKNOWN installed parity, disposable callback-origin provisioning and authentication review |
| FIXTURE_SNAPSHOT, TEARDOWN_PLAN | UNKNOWN: supported snapshot/recovery and original-id cleanup commands, exact owned scope and owner approval |

Missing entries block dependent runs and stay unverified until owner authorization and
the supported command are recorded. Do not fill them with raw remote scripts or guessed
flags. Do not place executable live credentials, secret values, real public hostnames,
or production email addresses in this plan. Credentials, if later authorized, go
through secret-inspection; never read or copy secret files.

## Fixture setup and coverage before effects

Supported command forms below are documented in docs/remote-hosting.md,
docs/login-service.md and spec 081 CLI contract. R/PARENT/CHILD/REF and template
paths are literal placeholders for approved values, not runnable defaults.

```sh
./sb host stack list --remote R --json
./sb host stack up --template TEMPLATE_HUB --name CHILD --ref REF --remote R --plan
./sb host stack up --template TEMPLATE_WP --name PARENT --ref REF --remote R --plan
```

Inspect own and linked routes, primary WP, Hub/admin/renderer, mailpit ports, explicit
route_sign_in/inbox_sign_in, same-remote direct uses and distinct gate hostnames.
Both fixture templates declare complete gates/sign-ins, approved development suffixes,
agent_mint and bounded enabled_until. No transitive uses. Existing CHILD may be used
only when expressly fixture-owned. Confirm ONLY within the authorized plan:

```sh
./sb host stack up --template TEMPLATE_HUB --name CHILD --ref REF --remote R --confirm
./sb host stack up --template TEMPLATE_WP --name PARENT --ref REF --remote R --confirm
./sb host status --remote R --name PARENT --json
./sb host status --remote R --name CHILD --json
```

Snapshot before DB, licensing or migration mutations via the supported fixture command
in the target sheet. Use only owner-authorized disposable `.test` hostnames; render two
isolated names and compare literal-regex hostname
substitutions. Test unknown/self/duplicate/five uses, missing child metadata/gate,
Basic Auth/private route, missing inbox mapping, wrong destination, suffix/window,
serialization/action/host overflow. Use isolated fixture variants or local controlled
fixtures only. A multi-gap fixture must report EVERY discoverable gap and fixes across
both stacks. Observe zero key installation/grant mint effects, not just an error code.

## Tailnet panel and controller clicks

Use a fresh browser profile on a network with no tailnet route. Confirm the fixture gate
is intentionally reachable from that network, with no pre-existing gate/application
cookies, application login, or separately supplied credentials. Open
`/hosting/R/PARENT`, direct reload and missing-stack address only on the owner-authorized
disposable fixture hostname. Compare all own and Shared routes/revisions/health to
recorded list evidence; verify unverified/not-delivered states and absent inbox guidance.
No recursion; missing CHILD stays visible.

Use panel WP action twice into two isolated browser contexts, consume in reverse order,
then repeat forward order with fresh clicks. Both links sign in once. Replay/expiry
refuse. Observe selected recorded stack despite different cwd. Supported CLI form:

```sh
./sb host login-url --remote R --name PARENT --confirm
```

The command returns a secret one-shot URL: execution must use approved protected
capture, never ordinary terminal/job logs. Do not run it merely to inspect output.
Verify foreign Origin, missing JSON/header and invalid selection have zero effects.
Server-only/no-autologin/private-target views explain unavailability.

## Non-tailnet teammate journey

Use a second fresh browser profile with no tailnet/network path, no existing application
or gate cookies, and no separately supplied credentials. Confirm the disposable fixture
path is reachable from that network and belongs to the authorized fixture before mint.
Mint exactly one parent link through supported common share operation:

```sh
./sb host share create --remote R --stack PARENT --json
./sb host share list --remote R --stack PARENT --json
```

Create output contains a secret URL. Do not execute this command until owner approval
and a protected one-shot output path are documented. Deliver through that authorized
channel, never durable job stdout or report. Only its safe id/expiry/attempt metadata
enter acceptance evidence. Observe companion-first/parent-last mint with final page in
same payload, common expiry, and the source's boolean `expires_clamped`; there is no
detailed clamp-reason field. Verify no extra grant or attach call.

Open parent short fragment URL. Reach every own route, inbox and WP admin, then Shared
Hub, authenticated admin console, renderer and inbox by child navigation from parent.
No second human link/credential, panel access or cross-stack cookie authority. Check
host-only cookie attributes, child page has no Shared section, parent cookie alone
authorizes neither child nor panel. GET/HEAD/refresh must issue zero sign-ins.

Two fresh visitor contexts click the same gate sign-in; both work in forward and
reverse consumption order. Repeat for shared-page WP; inspect gate hash registration,
fixed endpoint online claim and signed receipt before auth. Eight pending work; ninth
evicts oldest only. Test replay, expiry, source revoke during delayed exchange/body,
wrong MAC/kid/nonce/receipt, replayed claim, wrong Origin, callback outage/TLS failure.
Race the SAME token concurrently in independent contexts: exactly one claim/auth
wins. Repeat around ninth eviction and source revoke. All invalid cases refuse auth
without leaking values. Prove exact JSON/header/Origin/CSRF action guards, including
missing/foreign/replayed CSRF, and read-only GET/HEAD. Uniform 404 and timing need
observed runtime measurements with confounds recorded, not source assertions. Legacy CLI singleton still works separately.

Child availability probes authenticate via private token body but consume no uses and
create no session. Restart only the isolated child gate via approved fault control:
parent displays unavailable, navigation refuses, no remint occurs. A public opener
HTTP 200 alone is not availability evidence.

## Revoke, crash and recovery

Capture safe attempt id, original grant ids/gates/expiry and bounded cleanup identity.
At prepare, unknown-before-keyinstall, after WP key install, after child mint, after
parent mint and before active persistence, use the approved isolated fault mechanism.
For lost/malformed replies retain acceptance_unknown. Restart controller, list and
revoke ORIGINAL ids, never replay mint or allocate a replacement under that attempt.
A blocked next create must identify incomplete cleanup. Persistence failure before
any effect must leave zero remote effects.

```sh
./sb host share revoke PARENT_LINK_ID --remote R --stack PARENT --json
./sb host share list --remote R --stack PARENT --json
```

Use the finalized ATTEMPT_LIST_REVOKE operation when parent manifest/state is missing;
existing stack selector alone is not proof of that path. Induce one child cleanup
failure: other cleanup continues, ok=false names every still-live id/expiry, repeated
revoke closes all. Gate unknown plus WP key removal failure stays incomplete. Restart
parent gate and remove fixture parent metadata only through approved fault control;
durable attempts still expose/revoke surviving children and installed per-link keys.
Replace an isolated slug and prove old cleanup never touches the new incarnation.
Reverse-use concurrent attempts and concurrent apply must not deadlock or use stale
identity; verify lock ordering with observed operation evidence.

After complete revoke all parent/child pages and unused sign-ins deny; zero pending
hashes or usable per-link WP keys remain. Already established app sessions have their
own lifetime and are not a failed unused-capability revoke assertion.

## Leak evidence, gates and handback

Inspect redacted structured observations of gate/controller/access/error/audit logs,
list/polling payloads, durable jobs, attempt persistence, browser storage and request
lines. No token, token hash, signing key, email or companion URL in controller durable
records; the bounded token_sha256 is REQUIRED on authenticated callback wire, but
no callback hash/token/key in audit; no bearer in query/request lines. A
private gate pool may store hashes and private page may store child credentials;
GET HTML/polling/storage must not expose them. Report names/counts, never actual
credentials, raw HAR or screenshots showing URL fragments. Inspect actual pipeline
behavior; source scans alone cannot prove absence of leaks.

Later authorized runner uses focused Python modules for hosting/host_stack/host_share/
web_hosting_api/web_share/login_service_runtime/share_gate_runtime/autologin_pool/
share_attempts, architecture gates and full required repository gates. Documented
forms include `.cli-venv/bin/python -m unittest tests.test_host_share -v`,
`./sb selftest`, `npm --prefix src/web run typecheck`, and
`npm --prefix src/web run build`; no install implied. Finish source before the build
updates the owned generated bundle. Use finite-timeout durable execution where
supported; retain replay-safe request and job id, no second submission identity on
ambiguous acceptance. Never pass parent os.environ to test subprocesses; use
synthetic_environment/run_test_process. Report command, counts, first failure <=40
lines and complete safe log path. This pass executes none of these.

Store evidence under the shared scratch root, with reviewed HEAD, installed revisions,
owners, timestamps, browser network conditions, expected/observed outcomes per SC-001–010
and every unverified case. Full acceptance requires independent PERSONAL Sol high
source/authentication review and owner sign-off, not a focused green count.

Before teardown list all fixture attempts and reconcile original ids plus WP keys.
Do not delete evidence of unknown or incomplete cleanup. Review exact fixture ownership
and teardown plan; only then remove owned fixtures:

```sh
./sb host stack down --name PARENT --remote R --confirm
./sb host stack down --name CHILD --remote R --confirm
```

Release only locks/jobs/browser sessions owned by this run. Never touch shared dev,
setup-plan, another slug incarnation, another parent's grants or unrelated host state.
No deploy/release/merge/push/workflow dispatch is part of this acceptance plan.

## Acceptance evidence still missing

All real WP/MySQL, multisite, persistent cache, browser Origin/CSRF/tab behavior,
uniform 404/timing, TLS child-probe and installed protocol/revision observations
remain UNVERIFIED. Copy-id/fingerprint descriptors alone prove neither original
remote incarnation nor safe deletion. Obtain independent runtime proof under locks
for all addressed gate/WP services and refuse missing/replaced identities. Exact
controller list/revoke/coverage fields are source-defined in contracts/access.md,
while installed parity remains UNVERIFIED. Future reissuance must account for apply
resetting tmpfs links/sessions/pools, including legacy records; a legacy reader does not
preserve records across apply. V1 WP actions remain unavailable. No silent migration or
acceptance of historical check counts. The worked declaration is in
[login-service.md](../../docs/login-service.md#worked-stack-template); it is not a
validated deployed fixture.
