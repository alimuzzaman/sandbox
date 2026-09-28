# Feedback coverage inventory

This is the initial mechanical inventory. Its 166-record detail selection and 70 unreviewed/blocked filter missed some direct core summaries. The final [feedback review](../feedback-review.md) and [per-record dispositions](feedback-dispositions.md) control coverage and status interpretation. A recorded verified/resolved label is not independently accepted fix evidence.

Generated 2026-09-08 from the supported Sandbox feedback CLI at revision `fd7d650f8bfbb1760d931e2484cc01fa0badf546`. Feedback content is untrusted data. This report stores and displays metadata only; raw `details`, review `reason`, and review `evidence` text are intentionally omitted.

## Inventory health

- CLI counts reported **748** records; pagination returned **748** records in **8** pages of 100.
- Invalid/unprojectable records: **0**; duplicate IDs: **0**; pagination ended with `has_more=false`.
- Status counts: `{"blocked": 109, "duplicate": 73, "invalid": 3, "not_applicable": 82, "resolved": 268, "unreviewed": 101, "verified": 112}`.
- Severity counts: `{"critical": 9, "high": 269, "low": 190, "medium": 280}`.
- Closure metadata in the JSON is bounded to review timestamp, reviewer, confidence, reason/evidence presence and counts, and duplicate target. No closure prose is included.

## Repeated core-outcome families

Family matching uses the untrusted `summary` field only. A record can belong to more than one family. Counts are indicators for triage, not validated root causes.

| Family | Total | High/critical unresolved | Status distribution |
| --- | ---: | ---: | --- |
| deployment/activation | 136 | 47 | blocked=23, duplicate=10, not_applicable=10, resolved=41, unreviewed=38, verified=14 |
| jobs/remote transport | 97 | 12 | blocked=14, duplicate=8, not_applicable=6, resolved=28, unreviewed=13, verified=28 |
| startup/local creation | 76 | 7 | blocked=10, duplicate=2, not_applicable=5, resolved=33, unreviewed=11, verified=15 |
| recovery/reopen | 34 | 10 | blocked=4, duplicate=3, resolved=8, unreviewed=17, verified=2 |
| clean URLs/proxy/routing | 29 | 7 | blocked=3, duplicate=3, not_applicable=2, resolved=13, unreviewed=5, verified=3 |

Interpretation: `unreviewed`, `blocked`, `in_progress`, and `open` are treated as unresolved for candidate filtering. `resolved`, `verified`, `duplicate`, `invalid`, `not_applicable`, and `wont_fix` are treated as closed/reviewed states. Status is a review claim from an append-only local log, not independent proof that the underlying issue is fixed.

## High/critical unresolved candidates

Found **70** unique high/critical records whose summary matches at least one core family and whose status is unresolved. The list is metadata-only.

| Created | ID | Severity | Status | Family | Summary |
| --- | --- | --- | --- | --- | --- |
| 2026-09-08T16:36:01.541913Z | `0f0960d8c6ce36083fb195fb4a9fb3de` | high | unreviewed | startup/local creation, deployment/activation | Hosted deployment initializer compares incompatible Compose identity formats |
| 2026-09-08T16:23:28.394294Z | `35ee84bb6ae44f5b7e2e590f3b45ea72` | high | unreviewed | recovery/reopen | Restore diagnosis required repeated controller updates for missing safe evidence |
| 2026-09-08T15:47:57.198041Z | `8e73e0097028218b3216103c86596b9b` | high | unreviewed | clean URLs/proxy/routing | Real WordPress lifecycle smoke exposes stopped registry and intermittent clean route failure |
| 2026-09-08T15:34:54.681781Z | `08771395ef4d93355a5c53d9dfe3f893` | high | unreviewed | deployment/activation | Production status and apply logs cannot establish the last successful Lenzora deployment |
| 2026-09-08T15:29:38.569445Z | `4ae99c62c095ceb966f053c297343e1a` | high | unreviewed | deployment/activation | Fresh production recreate refused wp-permissions initializer as foreign evidence |
| 2026-09-08T09:40:42.369510Z | `6c8b5ebfab40731d9b245eacbcfbdfbe` | high | unreviewed | recovery/reopen | Registered PostgreSQL recovery profile lacks a concrete capture and isolated restore adapter |
| 2026-09-08T04:55:06.432492Z | `66035a8ff701c7010103f76ab117d838` | high | unreviewed | recovery/reopen | Hosted recovery may reuse stale content across backup sets |
| 2026-09-08T04:53:14.332785Z | `372b578caf0b653ccf63cbc1271448f4` | high | unreviewed | recovery/reopen | ASB recovery capture hides stale controller diagnosis |
| 2026-09-08T04:29:55.523812Z | `4d413785f6471c09424195af3ca1755e` | high | unreviewed | deployment/activation, recovery/reopen | Image recovery lacks read-only active transaction status |
| 2026-09-07T13:25:15.020216Z | `1f0321b04881029b992f0ae7dafe83cc` | high | unreviewed | startup/local creation | host apply final Compose convergence leaves dependent services in created state after 300s timeout |
| 2026-09-07T09:28:33.244222Z | `ae5d9f9f931491295cb69a91e4517a60` | high | unreviewed | jobs/remote transport | sb test prints only a job id and exits 0 while the run is still queued/running — looks like a pass |
| 2026-09-07T07:33:02.088370Z | `171fd36929aa7c319b848232afbba857` | high | unreviewed | deployment/activation | Image activation signs stale runtime environment without manifest or source revision refresh |
| 2026-09-07T07:17:04.875716Z | `bc5f47704c2f81de3b09a8429c7e5bf4` | high | unreviewed | deployment/activation, clean URLs/proxy/routing | Production effect_unknown persists after verified profile resolver fix |
| 2026-09-07T06:16:54.431113Z | `cbf25bd81d275d1502af3076b0bd609d` | high | unreviewed | deployment/activation | Production activation exact 821 returns effect_unknown |
| 2026-09-07T06:06:27.704530Z | `1135f1bd5461c35c9998bea370a59bf3` | high | unreviewed | deployment/activation | Development exact revision healthy but edge purge provider_auth refused |
| 2026-09-07T05:45:36.005571Z | `7dc12e973591c4f46d1fe0be41ca4d46` | high | unreviewed | deployment/activation | New signed Lenzora production image activation refuses artifact_invalid |
| 2026-09-07T05:37:53.222731Z | `29031eb6f69e28ef4aea7ec5fdad2b3c` | high | unreviewed | recovery/reopen | Ordinary host apply silently loses recovery authority on partial resource status |
| 2026-09-07T05:34:57.731493Z | `3126f1d654a039c32481c0522d831870` | high | unreviewed | recovery/reopen | Fresh pinned host apply cannot enter observation recovery |
| 2026-09-06T21:09:46.632262Z | `cbb01af7d11578bc2c46a257c2961d3f` | high | unreviewed | deployment/activation | Image provisioning hides controller revision mismatch as artifact_invalid |
| 2026-09-06T21:00:08.486187Z | `b7ae36b87191b02dcb7a430b8f838116` | high | unreviewed | deployment/activation, recovery/reopen | Initial image edge bootstrap rejects effect-free recovery history |
| 2026-09-06T16:10:13.170896Z | `0062bddad85e9b8091b4baa569793274` | high | unreviewed | deployment/activation | Production activation bundle refuses after successful immutable image staging |
| 2026-09-06T15:17:43.134927Z | `5e8d5502513f42ebdbaf5b2525834e42` | high | unreviewed | deployment/activation | Production image staging returns observation_invalid after clean terminal cleanup |
| 2026-09-06T14:09:47.537302Z | `3bb47f37f9a1bad8fe538612b89daeb8` | high | unreviewed | deployment/activation | Production image staging returns observation_invalid after clean terminal cleanup |
| 2026-09-06T14:09:11.593896Z | `4bde915ebfb4882e63ad9306765c0fda` | high | unreviewed | deployment/activation | Production image stage retry terminates with observation_invalid |
| 2026-09-06T10:53:13.427897Z | `192679602e758ba1a1cb59c50248fadc` | high | unreviewed | deployment/activation | Production image staging blocked by capacity with no verified cache reclaim path |
| 2026-09-06T10:31:13.647133Z | `026f17d7be0be8d1ceb5db9fb9d0c5ee` | high | unreviewed | deployment/activation | Hosted image stage-bundle refusal masks runtime revision mismatch |
| 2026-09-06T09:37:51.544140Z | `81d085d28ce5e5e84924787493084de1` | high | unreviewed | jobs/remote transport | sb test without --local fails at job submission: supervisor_launch_failed (remote_job_transport_error) |
| 2026-09-06T09:17:13.358869Z | `d7bae9124e5e49ac2cd6b0886e0522b6` | high | unreviewed | recovery/reopen | Legacy Hermes Drive backup scans stale registry instances |
| 2026-09-05T10:01:13.584844Z | `00efae8d734cef3b5a5aab10bced0192` | high | unreviewed | deployment/activation | HSSB remote CI preflight blocks artifact paths and misclassifies validation steps |
| 2026-09-05T05:12:10.710373Z | `6704dc49fbf1d94c5578a7857e34010e` | high | unreviewed | deployment/activation | ensure JSON leaks local login credential before plugin activation failure |
| 2026-09-03T01:32:08.774480Z | `1057af13a2afe8caa2dd825ef6f44b89` | high | unreviewed | deployment/activation | Production OCI stage-bundle provisioning refused with helper_failed |
| 2026-09-03T00:41:57.698825Z | `4a73fa1723f29ff4c9d8b93227599d1c` | high | unreviewed | deployment/activation | Lenzora production wrapper loses first protected OCI provisioning refusal |
| 2026-09-02T11:27:17.358573Z | `60b58eea1c85b3578c50bd031ea491a4` | high | unreviewed | deployment/activation | remote deploy cleanup loses ensure failure cause |
| 2026-09-02T11:24:02.213480Z | `5ea5623e364eaa397c438a7ece0450ee` | high | unreviewed | deployment/activation | deploy label is ignored for remote WordPress ensure |
| 2026-09-02T06:00:53.111997Z | `6cc6f10ce7cd1d1f3e6da51294caca6c` | critical | unreviewed | deployment/activation | activation gateway 404s every request carrying a query string (forward_auth keeps the query) |
| 2026-09-01T13:01:43.560296Z | `d10b637f22211ec447c5ece88e57e506` | high | unreviewed | clean URLs/proxy/routing | Local .tst https vhost returns 404 for any URL with a query string (BaseHTTP upstream via Caddy) |
| 2026-09-01T06:09:53.203734Z | `c07029871fadf960acfbceb83d2d3260` | high | unreviewed | startup/local creation, deployment/activation | Production apply stopped after clean exact-SHA plan with generic readiness error |
| 2026-08-31T11:47:18.889117Z | `2b98ef8ff6bd3aa71d13f75ade6d6d9b` | high | unreviewed | deployment/activation | Follow-up 1b5c9e9e: remote deploy failure is not multisite-specific; stale network state survives instance cleanup and --label is ignored |
| 2026-08-31T11:42:49.329002Z | `1b5c9e9e54b37db9021499aa080a7e68` | high | unreviewed | deployment/activation | Remote deploy --ensure fails for multisite:"subdirectory" projects: DOMAIN_CURRENT_SITE does not match the installed site |
| 2026-08-31T05:41:33.289687Z | `b572b3d55192b8bc9e7c70dec0722d9f` | critical | unreviewed | clean URLs/proxy/routing | sb preview create wrote its domain into a DIFFERENT existing instance's siteurl/home, breaking that instance's admin |
| 2026-08-31T05:33:58.543715Z | `e354cbc7b4e8f64af3d4f6bca20f5fc6` | high | unreviewed | clean URLs/proxy/routing | sb preview create returns ok with an HTTPS URL that can never handshake when the base domain is a third-level name |
| 2026-08-31T05:27:48.402070Z | `af7cf95d81966d8f6bde8c29610e84b4` | high | unreviewed | deployment/activation | sb wp has no --remote in 0.2.2, but the sandbox-cli skill documents it as the post-deploy path |
| 2026-08-30T23:46:06.896555Z | `b55e170130c402c0c4c876d58089968e` | high | unreviewed | startup/local creation | Hosted Compose autologin targets public service instead of WordPress |
| 2026-08-30T18:56:36.524391Z | `a866f505d377eb84cd972879b6acce9d` | high | blocked | jobs/remote transport | Remote resource scan acceptance loses structured job envelope |
| 2026-08-30T11:50:40.725556Z | `7ba71712a512c41794c72116ab503b70` | high | blocked | jobs/remote transport | Remote direct job supervisor launch failed twice before execution |
| 2026-08-30T10:52:25.494362Z | `82fb70e7e901d00e7511f411c6b1af4e` | high | blocked | deployment/activation | Remote multisite provisioning cannot write marker file |
| 2026-08-30T07:54:41.045674Z | `0e96ca5cba4bdcf2ba4cad23641790e2` | high | blocked | jobs/remote transport | Remote job registry retains expired jobs as active or queued |
| 2026-08-30T07:51:18.997651Z | `874a29b7373d68b97503ddfea760f04e` | high | blocked | deployment/activation | Provisioned remote cannot execute advertised headed E2E |
| 2026-08-25T15:00:19.195175Z | `9294d65a6985ed3eacf1353b11c1febb` | high | blocked | clean URLs/proxy/routing | Hosted Lenzora dev apply times out during Caddy reload after application services become healthy |
| 2026-08-25T14:37:36.329946Z | `95acb85a0e1cbbc855c7b8e508fbe73b` | high | blocked | jobs/remote transport, startup/local creation, deployment/activation | Hosted Lenzora dev deploy times out during compose restart and leaves worker runtime failures |
| 2026-08-25T07:14:16.408818Z | `74b4203d8e54a68aced7d0f164bcc652` | high | blocked | deployment/activation, clean URLs/proxy/routing | Every .tst instance URL returns 404: proxy forward_auth /v1/activate 404s for known route IDs |
| 2026-08-23T12:52:52.728146Z | `0cca9b50cc59c730b17ab11625fcd380` | high | blocked | deployment/activation | WordPress apply failed reachability and rollback for xspeed review instance |
| 2026-08-23T11:37:28.288234Z | `81a2e388d2aae43cebb46b0d206e2a31` | high | blocked | deployment/activation | WordPress apply rollback leaves CSS-capture instance unreachable |
| 2026-08-23T09:56:25.752158Z | `c5b8e6a908f458f3ccc5b2236b0b39f1` | high | blocked | deployment/activation | Remote Sandbox exact-head deploy hangs while ensuring instance |
| 2026-08-23T09:43:22.534987Z | `52b78220ec4255f8d680b707ca409980` | high | blocked | deployment/activation | Exact-head xSpeed PR review plugin activation fails in disposable Sandbox |
| 2026-08-22T16:02:42.176694Z | `5e44095151d103a4e1d13f043ea83a27` | high | blocked | deployment/activation | Hosted development apply reached rollback with network-unreachable SSH |
| 2026-08-22T13:53:28.940795Z | `7ab76b8b2a5191b77843b986f25d29c0` | high | blocked | jobs/remote transport | Hosted dev apply stalled building missing worker images from multi-GB context |
| 2026-08-22T09:10:56.340765Z | `cfdb4f37418aaf17cd06a584e6e99dd6` | high | blocked | deployment/activation | Lenzora hosted deployment failed because remote SSH became unreachable during apply and rollback |
| 2026-08-20T20:47:32.635297Z | `0eab73b892802256d2ba001497ad2998` | high | blocked | jobs/remote transport, startup/local creation | Failed remote Compose job loses Jest failure evidence |
| 2026-08-20T18:15:10.285808Z | `b4323966b25994dcd75e21650ab77f93` | high | blocked | jobs/remote transport, deployment/activation | Remote deployment job cannot be observed from Sandbox durable ledger |
| 2026-08-17T17:46:29.358124Z | `fb17bb5c05c60ef78ce1e33e7a25685b` | high | blocked | deployment/activation | hosted apps need a fast source-sync path: watch-and-push to deploy-src without a full apply |
| 2026-08-15T17:44:21.744370Z | `689840ec0c3890f568b130ddc0fd338e` | high | blocked | deployment/activation | lenzora.dev host deploy failing due remote disk pressure |
| 2026-08-15T05:16:48.899590Z | `9c28c05a8b08c0aedd6e51e496cc8424` | high | blocked | deployment/activation | Remote Sandbox revision sync is blocked because the provisioned controller is unreachable |
| 2026-08-14T15:34:18.016157Z | `6a1cca016d14d5cb8cb8f17648d15fc4` | high | blocked | jobs/remote transport | Remote capacity and active-job monitor transport unavailable |
| 2026-08-13T12:27:11.785553Z | `fee3a2f77e25dcba833e6242976a0b8a` | high | blocked | recovery/reopen | Remote SSH banner exchange timed out during retained evidence recovery |
| 2026-08-13T12:07:12.595415Z | `25008adab7a0612dca8f360ea6c27663` | high | blocked | jobs/remote transport, startup/local creation, deployment/activation | Fresh remote Feature019 job still cannot provision Compose network |
| 2026-08-13T11:42:29.304449Z | `e66a25a7442e753015cd3a22d40f74af` | high | blocked | deployment/activation | Remote workspace references missing deployed project directory |
| 2026-08-13T11:34:37.678432Z | `498b193cc569470ec87feba685af3dea` | high | blocked | deployment/activation | Remote deploy blocked: Docker daemon out of address pools (31 networks) on scaleway-sandbox |
| 2026-08-13T09:15:41.725213Z | `b06bb2505db8cfe037fe4af3f908f5c6` | high | blocked | deployment/activation | Remote workspace list resolves a missing deploy path |
| 2026-08-13T08:30:45.898004Z | `76b5ac739bf331866795b3407295dff5` | high | blocked | jobs/remote transport, deployment/activation | Exact-source deploy Git push times out before remote job creation |

## High/critical reviewed or closed comparison

Found **96** unique high/critical records in reviewed/closed states whose summaries match a core family. These are shown to distinguish historical resolved claims from current/unreviewed claims; no closure prose is emitted.

| Created | ID | Severity | Status | Family | Summary |
| --- | --- | --- | --- | --- | --- |
| 2026-08-30T22:50:55.383593Z | `3197a23aa4d92f7f07c135c7166aa75a` | high | duplicate | recovery/reopen | Amar Sonar recovery profile plan returns invalid remote inventory |
| 2026-08-25T17:39:15.069297Z | `e608c783333b62a9e4e6aad09e784ac1` | high | duplicate | deployment/activation | Activation enable fails while the existing supervisor is healthy |
| 2026-08-25T17:34:42.106792Z | `a562850cc9e3fed23450d74aa6ded0dd` | high | not_applicable | clean URLs/proxy/routing | Ensure silently rewrites a clean domain back to localhost when proxy setup falls back |
| 2026-08-25T15:02:24.678638Z | `74f5a1b8a8da1d6a90bc081bf3d2e3f4` | high | resolved | startup/local creation, deployment/activation | Activation supervisor restart fails while health state transitions to inactive |
| 2026-08-25T14:08:11.689699Z | `a6f1f5a8c4611f430d6963b08d4e791a` | high | resolved | clean URLs/proxy/routing | domains up reports clean ingress unavailable when another listener answers the hostname probe |
| 2026-08-25T13:58:04.405359Z | `b459d15a68e801aa605881b9864a81ee` | high | verified | jobs/remote transport, deployment/activation | Hosted Lenzora deploy job is accepted but branch guard and job ledger prevent verification |
| 2026-08-25T10:32:12.735848Z | `c0037db18a5c0485b646df051a1cdd59` | high | resolved | deployment/activation | Remote deploy of a composer-autoloaded WP plugin boots to a fatal: vendor/ is gitignored, so PSR-4 classes are missing |
| 2026-08-25T09:27:50.265266Z | `ae744b9b19f4a1de4d85735bd83693a1` | high | resolved | clean URLs/proxy/routing | Stale mkcert certificate forced broken HTTPS redirect for plain .tst route |
| 2026-08-25T08:24:45.556744Z | `7c8f8b0b7896b8a72981a4d5b097a0dc` | high | resolved | clean URLs/proxy/routing | clean URL ingress can be intercepted by OrbStack wildcard ports |
| 2026-08-25T06:04:56.553956Z | `529a0ed3d6697d12c2c958100c09369f` | high | resolved | startup/local creation | Remote service migration times out while staging current runtime |
| 2026-08-25T05:55:48.329488Z | `3a6cdc0284ae86c26c8bdbf27041457f` | high | resolved | deployment/activation | Deploy symlink defect reproduces on a clean instance, and the only workaround deadlocks future deploys on ownership |
| 2026-08-25T05:51:17.673375Z | `6987343f2dcacaedf7edc59ad81aa9d3` | high | duplicate | clean URLs/proxy/routing | Domain setup hits pre-existing XSpeed fatal during URL reconciliation |
| 2026-08-25T05:42:07.470658Z | `1f5484a5bd4548de4514719d62615605` | high | resolved | deployment/activation | sb deploy symlinks the plugin to a deploy-src path that is not bind-mounted, so WordPress cannot see it |
| 2026-08-25T05:18:20.034374Z | `f56063057f25aca4696e8abdbdd5b160` | high | resolved | deployment/activation | Local idle-stop authority remains inactive when opted-in routes lack activation credentials |
| 2026-08-24T17:53:57.134420Z | `a05d10d59bf2e9bc1b49c58696bf752e` | high | resolved | deployment/activation, clean URLs/proxy/routing | activation scan fails on invalid route metadata |
| 2026-08-24T16:57:59.293216Z | `ead67171123ba137eb48221d56cc0d46` | high | resolved | startup/local creation | Remote service migration hangs during runtime source upload |
| 2026-08-24T11:07:11.035627Z | `483a4fef967b99f8e1957030a35a00e1` | high | resolved | deployment/activation | sb test always routes to the remote deploy path; --local is ignored and the run dies on an SSH git push timeout |
| 2026-08-23T18:27:56.355258Z | `e7412a6dd087f348858f78348525f88e` | high | resolved | deployment/activation | Host apply cannot inject exact deployed source revision into runtime environment |
| 2026-08-23T13:26:30.967136Z | `9275f8ff5da38293d452fe4d9e28a995` | high | resolved | clean URLs/proxy/routing | Apply reported ready after WordPress critical error and lost pre-config instance routing |
| 2026-08-23T13:25:04.108784Z | `f0b3e8fdf1023c0585b5ce145716ead5` | high | resolved | startup/local creation | WordPress init guessed unavailable Elementor Pro dependency for xSpeed |
| 2026-08-23T13:20:14.911517Z | `f3a4d100828e51e70f4ea6e862329e58` | high | resolved | startup/local creation | Host logs fail when desired background service is absent |
| 2026-08-23T12:43:19.176365Z | `b067e765faaf880f32a7d1ee5c0539ac` | high | resolved | jobs/remote transport | remote job cancel fails with malformed control-plane error |
| 2026-08-23T11:38:35.586500Z | `1cfc73383f2132176c6dada6e6fde9e5` | high | resolved | startup/local creation | Failed apply starts web containers without database service |
| 2026-08-23T10:03:02.186600Z | `cfb94c42166cb64f3b45c2d8189f624d` | high | verified | startup/local creation | Remote ensure leaves no registered instance after creating compose resources |
| 2026-08-23T09:51:46.674719Z | `412f6c4c5a9eb9d337c8e73baf1ad2d0` | high | verified | startup/local creation | Disposable Free Sandbox reaches HTTP but WordPress install core download times out |
| 2026-08-23T09:43:29.940332Z | `9e9e30aa23570f8d7f50438d155b4cf9` | high | duplicate | deployment/activation | Fresh Sandbox cannot activate exact-head xSpeed PR source |
| 2026-08-23T09:40:25.438877Z | `b9d6995a1966fe8b31077bd124d23b9b` | high | verified | deployment/activation | Remote ensure cannot deploy detached exact-head review clone |
| 2026-08-23T09:40:04.845058Z | `00de46521aee1ff013aeabb5bd7de792` | high | not_applicable | startup/local creation | Sandbox init rejects repository without compose or recognized project descriptor |
| 2026-08-22T16:09:23.013983Z | `4ad5d6605243e8a23beca6339ad7a001` | high | verified | deployment/activation | Hosted apply retry timed out while resetting deployed source |
| 2026-08-22T13:44:50.134141Z | `9290217a7686140b82bdad36fa84fb55` | high | resolved | deployment/activation | Detached deploy output follow hung during process identity inspection |
| 2026-08-22T10:35:35.109817Z | `7437b8a82a701af8126b9b2d3dbaf1ae` | high | resolved | deployment/activation | Hermes launcher accepted no --max-turns despite pinned release guidance |
| 2026-08-22T09:15:33.291539Z | `cd7025da758c2f5dc564dcc320748195` | high | duplicate | jobs/remote transport | Ox Alpha autonomous Sandbox worker stopped after repeated empty model responses |
| 2026-08-22T05:52:07.909764Z | `cdb2e1844f9557c118b44f9d36d46f71` | high | verified | deployment/activation | Remote provision leaves obsolete cloudflared after successful completion |
| 2026-08-21T14:16:30.453582Z | `3d9a49cee63a623d9169eecfc6802610` | high | verified | jobs/remote transport | Remote job submission rejected by unsupported execution policy capability |
| 2026-08-21T02:28:45.614587Z | `3326ceaec6df31b36d18f9e2c229bf2c` | high | verified | jobs/remote transport | Remote job submission blocked by missing execution policy capability |
| 2026-08-20T23:46:43.061706Z | `9f0122e71b1ca2a956b20ab3442cf3fd` | high | resolved | startup/local creation | Scoped sb setup started unrelated project instances |
| 2026-08-20T20:20:54.789467Z | `ac945dffe6fc473d30ee91368e301442` | high | verified | jobs/remote transport, deployment/activation | Deploy wrapper accepted an unobservable job after SSH timeout |
| 2026-08-20T18:24:04.169872Z | `45dcb0236e2ad64607251481e1b1ddf8` | high | resolved | jobs/remote transport | Reviewed Docker pool plan still blocks remote test job admission |
| 2026-08-20T18:21:32.823055Z | `e6a9bec15a1cc6be016d4ec18af5528d` | high | resolved | jobs/remote transport | Remote full-test job blocked by Docker network capacity admission |
| 2026-08-20T11:14:29.617592Z | `0ed665d09e60921f170c48162e2d488e` | high | resolved | deployment/activation | Remote deploy is permanently blocked by docker_network_capacity_unavailable even though the address pools already match the desired plan |
| 2026-08-20T10:19:35.052796Z | `17f8ea64f9128f062d3e6c9498945594` | high | resolved | jobs/remote transport | sb test routes to a remote runner by default and fails with NetworkCapacityAdmissionError, even with a healthy local instance |
| 2026-08-19T08:48:39.320247Z | `7acb4245dd8694fb4ee03b061db7e4ec` | high | verified | clean URLs/proxy/routing | sb apply --project-dir rolls back a valid config change: reachability probe false-negative on an https instance |
| 2026-08-17T17:26:24.507860Z | `a6223e1481c8800431839f6a4e891b80` | high | resolved | jobs/remote transport | host apply should emit incremental progress; the job runtime already proves the pattern |
| 2026-08-17T16:31:26.368656Z | `5fb893827632bc14bb55a7f051410723` | high | resolved | deployment/activation | sandbox is adding steps for hosted apps: no deploy diagnose command, and secrets run cannot inject a credential pair |
| 2026-08-17T16:31:05.068757Z | `37d95e663fb02755608c87d5715d67f7` | high | resolved | deployment/activation | host apply is silent for 30+ minutes and discards build output, so every deploy failure needs a manual forensic session |
| 2026-08-17T16:30:51.405083Z | `c158edba811694913fe4cb4ccc3bbe2b` | high | resolved | startup/local creation, deployment/activation | host apply recreated containers from a stale image; no build ran despite compose.build defaulting to true |
| 2026-08-17T09:24:32.533972Z | `6728d6f332f0cc7b76a4df0659236bc0` | high | resolved | deployment/activation | sb host apply always passes --build and dies at the fixed 900s timeout for a large Next.js image |
| 2026-08-16T10:53:12.914352Z | `71be94307c0b2dfee1ef2640898bf1a0` | critical | resolved | deployment/activation | sb host apply exits 0 after a failed deploy, and its rollback can fail silently when the disk is full |
| 2026-08-16T09:36:03.286283Z | `0b420c9bf187cdce7a9e8be03d44dfda` | critical | resolved | startup/local creation | target inference sent every project's sb ensure to the VPS instead of booting locally |
| 2026-08-15T06:14:04.182381Z | `30a6c1d1f35eab1308137243f8ef119b` | high | resolved | deployment/activation | Provisioned remote reported unreachable during required validation retry |
| 2026-08-15T05:33:07.767108Z | `b8fcedf19c65046b16ecd8d61877ad6c` | high | resolved | startup/local creation, deployment/activation | Confirmed remote provision returned no receipt and service health timed out |
| 2026-08-14T08:01:17.445605Z | `b2fac0b406d02626aead84c36ccfb4ad` | high | resolved | deployment/activation | Herd lifecycle bypasses host-compatible mu-plugin provisioning |
| 2026-08-14T05:12:45.270384Z | `6a6d2325ed47be0e1c73860e5432278c` | high | resolved | startup/local creation | Init with project-dir provisions wrong Docker target |
| 2026-08-13T14:53:44.413842Z | `3a6e8c1a1fa71bde1a74b8b0eeb37e9c` | high | resolved | deployment/activation | Remote workspace index remains incomplete after provision |
| 2026-08-13T14:18:45.246625Z | `3c4f9059a8509b919774072acb8aa63d` | high | verified | jobs/remote transport, startup/local creation | Direct-host T126 job-start returned no acceptance payload |
| 2026-08-13T14:13:13.491497Z | `38efeba54f4a86c202a045b7cc55020a` | high | verified | jobs/remote transport, startup/local creation | Direct-host T124 job-start returned no acceptance payload |
| 2026-08-13T13:57:34.643834Z | `c4237726db9330b9c59a0fbc30328790` | high | resolved | jobs/remote transport | Remote job-status SSH lookup timed out during accepted-job observation |
| 2026-08-13T13:21:15.003186Z | `5e985bc296c1a22a281d8d0ae11fd8a5` | high | resolved | deployment/activation | Remote deploy reached plugin activation but deployed plugin was absent |
| 2026-08-13T13:19:14.635746Z | `0384ab6b647c4b6f3ce1db9a348d496e` | high | resolved | jobs/remote transport, startup/local creation | Remote job-start intermittently returns no acceptance payload or durable ledger entry |
| 2026-08-13T12:58:07.060565Z | `d7e6cba07d96a9d0d7af2d993b547640` | high | verified | jobs/remote transport, startup/local creation | Direct-host job-start completed without acceptance payload |
| 2026-08-13T12:42:07.295650Z | `0139d6637973f4f1d7ceec87a2f45b75` | high | resolved | startup/local creation, recovery/reopen | Remote ensure fails at post-install baseline snapshot: mariadb-dump cannot write /snapshots (Errcode 13) |
| 2026-08-13T12:40:42.584764Z | `009845006beb244f191c461c3d75cf55` | high | resolved | jobs/remote transport | Remote job-list large page failed and surfaced a tail of retained job data as an error |
| 2026-08-13T12:26:27.849160Z | `dde35952937cff4f87cd77549d568878` | high | verified | jobs/remote transport | Remote job output cannot resolve Sandbox home over SSH |
| 2026-08-13T12:26:00.045675Z | `ab332c6de39e1d5c4fdd322ca1f5c057` | high | resolved | jobs/remote transport | Remote job status SSH control timed out after jobs completed |
| 2026-08-13T12:25:18.961163Z | `088652d49fdabbff77311353f251270b` | critical | resolved | deployment/activation, recovery/reopen | Docker pool rollback restored configuration but left 20 previously running containers stopped |
| 2026-08-13T11:52:33.255259Z | `ed693f7259cc768dd29fb4a01da89d48` | high | verified | jobs/remote transport | Detached remote exec returned no job result for standalone evaluator state |
| 2026-08-13T11:52:01.283476Z | `1a7dde8bc4c88c8f7f311bf11c5ab5f3` | high | verified | jobs/remote transport | Remote active job list can return a false empty page |
| 2026-08-13T11:37:06.538089Z | `1f094d2f708e7c57cf128ac9635aa5af` | critical | resolved | deployment/activation | Failed deploy leaves wrong remote instance registered |
| 2026-08-13T11:35:47.202390Z | `a1fc66d41f603894e18bc4092a0a9aba` | critical | verified | deployment/activation | Deploy ignores instance and provisions wrong project |
| 2026-08-13T11:33:06.365167Z | `f7e83e9e0dc00d0c08591d45f7eca1f5` | high | verified | deployment/activation | Deploy cannot reconcile diverged managed remote branch |
| 2026-08-13T10:16:55.912776Z | `25a58f04cd6d056e6cb02379810f9b18` | high | resolved | jobs/remote transport, startup/local creation | Direct job-start terminal output unavailable through remote transport |
| 2026-08-13T10:15:28.066844Z | `56a4b248c8c308ee4263e621eb897519` | high | duplicate | jobs/remote transport, startup/local creation | Direct-host remote job cannot start: Docker address pools exhausted |
| 2026-08-13T10:07:15.574811Z | `00be19feb659fa1989d757ee5e5b247d` | high | resolved | jobs/remote transport | Remote durable job output read fails after accepted submission |
| 2026-08-13T09:17:22.934433Z | `d1a8a9f50d980bc8b1be061ef20c52ae` | high | verified | deployment/activation | Remote deploy push timeout reproduced |
| 2026-08-13T09:14:58.420510Z | `1ad96d2d24f9e1aa0bc401dde35eb2f1` | high | verified | deployment/activation | Remote deploy Git push timed out |
| 2026-08-13T09:12:04.116591Z | `f1a869ebbf96377e09fb5f3dcee91c4c` | high | verified | deployment/activation | Remote deploy git push hardcodes a 120s timeout; large repos can never deploy |
| 2026-08-13T08:30:23.625158Z | `ab89060b777e09db660cec5cb2f40ba1` | high | verified | jobs/remote transport | Detached job submission hangs and exact workspace job-list returns empty object |
| 2026-08-13T08:19:04.237665Z | `665bb8da6b1d86c8e1754e1c85e94dd0` | high | resolved | startup/local creation, deployment/activation | Fresh wordpress@1 child-image build fails at mbstring and init resolves the wrong project root |
| 2026-08-13T08:06:35.277853Z | `6d5a269d610dcd57d4fe2df5a0223532` | high | resolved | startup/local creation | sb init ignored explicit project-dir and instance target |
| 2026-08-13T07:54:42.691376Z | `f599f5f468f6a9c45ed83dd04b4b363b` | high | verified | jobs/remote transport | Accepted T105 r6 job-status returned no payload |
| 2026-08-13T07:53:29.349222Z | `d1a8b56d500abb30aa94cb671c4cea64` | high | verified | jobs/remote transport | Accepted T105 r6 job output read failed |
| 2026-08-13T07:43:40.812403Z | `ab65eeee5a3c52338cebf41cc0e6361d` | high | verified | jobs/remote transport | T105 r5 job-list preflight still fails despite reported runtime alignment |
| 2026-08-13T07:32:12.908042Z | `9db5f6426342bffb1befd24f3a8c0d05` | high | verified | jobs/remote transport | T105 r4 remote compatibility probe still cannot list durable jobs |
| 2026-08-13T07:16:51.152956Z | `505bba6bf0a08db3d0e5762caa97bd7e` | high | verified | jobs/remote transport, startup/local creation | Remote job-start is blocked by project-identity protocol version skew |
| 2026-08-13T07:16:10.286901Z | `234acaf1e04881a87ec78b47555f6d1b` | high | verified | jobs/remote transport | Remote job-list client and direct host disagree on project-identity argument |
| 2026-08-13T06:51:42.653925Z | `01f10c6bdbb113f102f4d1583b2d3e93` | high | verified | jobs/remote transport, startup/local creation | Authorized corrected T105 job-start again returned no acceptance identity |
| 2026-08-13T06:50:38.970514Z | `0f423fb08b654a25161008d35dd30f34` | high | verified | jobs/remote transport | Remote job-list preflight cannot return old or fresh workspace records |
| 2026-08-13T06:49:50.388203Z | `7f392acceefd15b24d8b513e3825c87e` | high | verified | jobs/remote transport, startup/local creation | Fresh T105 job-start returned no durable acceptance identity |
| 2026-08-13T06:46:37.008196Z | `fc8b79070969844dba5548dc0b1fcdca` | high | verified | startup/local creation | Learning host apply stops at unhealthy background chat service |
| 2026-08-13T06:42:26.607000Z | `12e2ef47166e8deaa1cf5bbc698110d1` | high | verified | jobs/remote transport | Remote retained-output cursor read fails for accepted durable job |
| 2026-08-13T06:11:52.625986Z | `79d775b43cac5d1795b9a2fc52b0ff26` | high | resolved | jobs/remote transport | Remote job-list control fails before returning retained records |
| 2026-08-13T06:09:54.502251Z | `3da039b402a4df0d831239c97a3cb987` | high | resolved | jobs/remote transport | Detached remote job submission returned no job identity |
| 2026-08-13T05:27:29.414623Z | `343d1a5ae1c59b8f2754ee24ebf96b9d` | high | resolved | jobs/remote transport, startup/local creation | Remote job-start can return silently without durable acceptance |
| 2026-08-12T12:37:43.969421Z | `8291ab9c74350d34a0d5ed4ff65e9262` | high | resolved | startup/local creation | Nested host manifest applies Compose from wrong source root |
| 2026-08-12T10:56:29.964372Z | `822b93235e7583db404d32b4400ae894` | high | duplicate | startup/local creation, deployment/activation | Compose test provisioning blocks when Docker network pools are exhausted |
| 2026-08-12T09:50:43.673984Z | `adde58a69cc69c26071e2c8555edbb0a` | high | resolved | recovery/reopen | sb restore drops every table with no confirmation and no --yes flag |

## Limits

- The feedback log is machine-local and append-only; this run is a point-in-time inventory. The CLI count changed during prior inspection, so the count and paginated list are recorded together for reconciliation.
- Family labels are keyword-based over summaries. They can miss issues described without these terms or include incidental matches. Root validation belongs to the parent review.
- No feedback was reviewed, edited, submitted, pruned, or otherwise mutated. No secrets were accessed.
