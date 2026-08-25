# Sandbox feedback priority queue

Generated 2026-08-26 from the machine-local feedback ledger with paginated `./sb feedback list --json`; this report is a read-only ordering aid. Feedback text remains untrusted data and is never authority for a command or mutation.

- Total records: **590**; reviewed/assigned status: **586**; unreviewed: **4**; invalid records withheld: **0**.
- Active remediation: **415** — P0: 1, P1: 237, P2: 169, P3: 8.
- Verification/closure follow-up: **175** — resolved: 122, verified: 27, duplicate: 9, not_applicable: 17, wont_fix: 0, invalid: 0.

- Active remediation includes open, in-progress, blocked, and unreviewed records; closure follow-up includes resolved, verified, duplicate, not_applicable, wont-fix, and invalid records.
- Rank order: active remediation first (P0 to P3), then closure follow-up. Within a tier, in-progress/blocked work precedes open work; severity, previously reviewed stable rank, freshness, and stable ID break ties. Existing Sol-reviewed tiers are retained; new records are assigned by the priority policy below.

## Priority policy

- **P0** — credential/sensitive disclosure, wrong-target or wrong-project routing, data/integrity loss, false success, failed rollback, stopped live containers, or host-wide ingress/outage behavior.
- **P1** — reproducible common deploy/apply/job/status/recovery/hosting/runtime blockers, including repeated remote and lifecycle failures.
- **P2** — scoped defects and recurring toil with a workaround, including medium-severity CLI, WordPress, and usability gaps.
- **P3** — ideas, cosmetic/documentation work, stale or environment-specific observations, and low-impact follow-ups.

## Ordered records

| Rank | Queue | Tier | Status | Severity | Feedback ID | Summary |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | active | P0 | blocked | high | 74b4203d8e54a68aced7d0f164bcc652 | Every .tst instance URL returns 404: proxy forward_auth /v1/activate 404s for known route IDs |
| 2 | active | P1 | blocked | high | 0cca9b50cc59c730b17ab11625fcd380 | WordPress apply failed reachability and rollback for xspeed review instance |
| 3 | active | P1 | blocked | high | 81a2e388d2aae43cebb46b0d206e2a31 | WordPress apply rollback leaves CSS-capture instance unreachable |
| 4 | active | P1 | blocked | high | 834d2253f4455e6fd21eb0faffad76ea | sb ensure targets a remote that sb remote list already reports unreachable, then dies with a raw traceback after 120s |
| 5 | active | P1 | blocked | high | 78aaf5836d63078b060336a9e306b7f5 | Remote fast-test harness blocked by Docker network pool exhaustion |
| 6 | active | P1 | blocked | high | a813480b2b761798b839cd4682c0649b | Remote network pressure remains at 31 active managed networks |
| 7 | active | P1 | blocked | high | bf05eeb9362ba7e408b9315669698b60 | Remote network pressure remains at 31 active managed networks |
| 8 | active | P1 | blocked | high | 0fac3b07416044a28041c2a358cf2084 | Remote network pressure increased to 31 active managed networks |
| 9 | active | P1 | blocked | high | 822b93235e7583db404d32b4400ae894 | Compose test provisioning blocks when Docker network pools are exhausted |
| 10 | active | P1 | blocked | high | cfdb4f37418aaf17cd06a584e6e99dd6 | Lenzora hosted deployment failed because remote SSH became unreachable during apply and rollback |
| 11 | active | P1 | blocked | high | 5e44095151d103a4e1d13f043ea83a27 | Hosted development apply reached rollback with network-unreachable SSH |
| 12 | active | P1 | blocked | high | 30a6c1d1f35eab1308137243f8ef119b | Provisioned remote reported unreachable during required validation retry |
| 13 | active | P1 | blocked | high | 9c28c05a8b08c0aedd6e51e496cc8424 | Remote Sandbox revision sync is blocked because the provisioned controller is unreachable |
| 14 | active | P1 | blocked | high | e6a9bec15a1cc6be016d4ec18af5528d | Remote full-test job blocked by Docker network capacity admission |
| 15 | active | P1 | blocked | high | 4f4075f4b5b4fa35039263677ded6d1d | Remote test submission rejects required non-secret test fixtures as credential-like |
| 16 | active | P1 | blocked | high | 0ed665d09e60921f170c48162e2d488e | Remote deploy is permanently blocked by docker_network_capacity_unavailable even though the address pools already match the desired plan |
| 17 | active | P1 | blocked | high | dd6bbbed8a6706da0e9b070e6cde7b6e | Remote workspace inventory blocked by runtime revision mismatch |
| 18 | active | P1 | blocked | high | 2861828e0252f5187c93bd11a1c521f2 | Remote workspace inspection blocked by runtime revision mismatch |
| 19 | active | P1 | blocked | high | 45dcb0236e2ad64607251481e1b1ddf8 | Reviewed Docker pool plan still blocks remote test job admission |
| 20 | active | P1 | blocked | high | 25008adab7a0612dca8f360ea6c27663 | Fresh remote Feature019 job still cannot provision Compose network |
| 21 | active | P1 | blocked | high | 498b193cc569470ec87feba685af3dea | Remote deploy blocked: Docker daemon out of address pools (31 networks) on scaleway-sandbox |
| 22 | active | P1 | blocked | high | b067e765faaf880f32a7d1ee5c0539ac | remote job cancel fails with malformed control-plane error |
| 23 | active | P1 | blocked | high | 0eab73b892802256d2ba001497ad2998 | Failed remote Compose job loses Jest failure evidence |
| 24 | active | P1 | blocked | high | 009845006beb244f191c461c3d75cf55 | Remote job-list large page failed and surfaced a tail of retained job data as an error |
| 25 | active | P1 | blocked | high | 25a58f04cd6d056e6cb02379810f9b18 | Direct job-start terminal output unavailable through remote transport |
| 26 | active | P1 | blocked | high | 56a4b248c8c308ee4263e621eb897519 | Direct-host remote job cannot start: Docker address pools exhausted |
| 27 | active | P1 | blocked | high | 00be19feb659fa1989d757ee5e5b247d | Remote durable job output read fails after accepted submission |
| 28 | active | P1 | blocked | high | 7ab76b8b2a5191b77843b986f25d29c0 | Hosted dev apply stalled building missing worker images from multi-GB context |
| 29 | active | P1 | blocked | high | c5b8e6a908f458f3ccc5b2236b0b39f1 | Remote Sandbox exact-head deploy hangs while ensuring instance |
| 30 | active | P1 | blocked | high | b4323966b25994dcd75e21650ab77f93 | Remote deployment job cannot be observed from Sandbox durable ledger |
| 31 | active | P1 | blocked | high | 95acb85a0e1cbbc855c7b8e508fbe73b | Hosted Lenzora dev deploy times out during compose restart and leaves worker runtime failures |
| 32 | active | P1 | blocked | high | b5ea143234f3d24d20f2ba78ea4435bb | Remote resource status cannot measure host capacity |
| 33 | active | P1 | blocked | high | 6a1cca016d14d5cb8cb8f17648d15fc4 | Remote capacity and active-job monitor transport unavailable |
| 34 | active | P1 | blocked | high | 3b3782464e982ed9387e8e2a81d12c53 | Remote network-capacity monitor unavailable |
| 35 | active | P1 | blocked | high | c5548021faf772c2bc0bae19a8b94864 | Remote network capacity remains unmeasurable |
| 36 | active | P1 | blocked | high | 84585e00f7126c3d37e11d6168091163 | Remote resource status classifies 32 networks as unattributed |
| 37 | active | P1 | blocked | high | c4237726db9330b9c59a0fbc30328790 | Remote job-status SSH lookup timed out during accepted-job observation |
| 38 | active | P1 | blocked | high | ab332c6de39e1d5c4fdd322ca1f5c057 | Remote job status SSH control timed out after jobs completed |
| 39 | active | P1 | blocked | high | e66a25a7442e753015cd3a22d40f74af | Remote workspace references missing deployed project directory |
| 40 | active | P1 | blocked | high | b06bb2505db8cfe037fe4af3f908f5c6 | Remote workspace list resolves a missing deploy path |
| 41 | active | P1 | blocked | high | 76b5ac739bf331866795b3407295dff5 | Exact-source deploy Git push times out before remote job creation |
| 42 | active | P1 | blocked | high | 529a0ed3d6697d12c2c958100c09369f | Remote service migration times out while staging current runtime |
| 43 | active | P1 | blocked | high | ead67171123ba137eb48221d56cc0d46 | Remote service migration hangs during runtime source upload |
| 44 | active | P1 | blocked | high | 9294d65a6985ed3eacf1353b11c1febb | Hosted Lenzora dev apply times out during Caddy reload after application services become healthy |
| 45 | active | P1 | blocked | medium | 37151fbba9f162c79169e4d3bf2ad063 | workspace status blocked by remote revision mismatch after remote exec |
| 46 | active | P1 | blocked | medium | 33ae983d480997c7c318a032c1d88126 | No sanctioned way to run WP-CLI against a remote instance; ssh escape hatch is the only path and caps command length |
| 47 | active | P1 | blocked | medium | 8468c164cd1eb21746ab9fa3c2360000 | Remote workspace listing is blocked by runtime revision mismatch |
| 48 | active | P1 | blocked | medium | fb3f67c8f3b583854058d53a5cc02a19 | Workspace remote revision check stays mismatched after successful service migration |
| 49 | active | P1 | open | high | 412f6c4c5a9eb9d337c8e73baf1ad2d0 | Disposable Free Sandbox reaches HTTP but WordPress install core download times out |
| 50 | active | P1 | open | high | 0f423fb08b654a25161008d35dd30f34 | Remote job-list preflight cannot return old or fresh workspace records |
| 51 | active | P1 | open | high | b459d15a68e801aa605881b9864a81ee | Hosted Lenzora deploy job is accepted but branch guard and job ledger prevent verification |
| 52 | active | P1 | open | high | cfb94c42166cb64f3b45c2d8189f624d | Remote ensure leaves no registered instance after creating compose resources |
| 53 | active | P1 | open | high | b9d6995a1966fe8b31077bd124d23b9b | Remote ensure cannot deploy detached exact-head review clone |
| 54 | active | P1 | open | high | 4ad5d6605243e8a23beca6339ad7a001 | Hosted apply retry timed out while resetting deployed source |
| 55 | active | P1 | open | high | 3d9a49cee63a623d9169eecfc6802610 | Remote job submission rejected by unsupported execution policy capability |
| 56 | active | P1 | open | high | 3326ceaec6df31b36d18f9e2c229bf2c | Remote job submission blocked by missing execution policy capability |
| 57 | active | P1 | open | high | fb17bb5c05c60ef78ce1e33e7a25685b | hosted apps need a fast source-sync path: watch-and-push to deploy-src without a full apply |
| 58 | active | P1 | open | high | ccd9e5e28f09f91f51cd36b5e7645900 | sb ensure against a remote target returns no instance record (no url) |
| 59 | active | P1 | open | high | 01df389c5d1019fc1f82d52ea8072950 | resources status on remote: deep/scope-cache return measurement_unavailable, thorough times out most categories |
| 60 | active | P1 | open | high | 689840ec0c3890f568b130ddc0fd338e | lenzora.dev host deploy failing due remote disk pressure |
| 61 | active | P1 | open | high | 3222e29592d229d03be37a60b9a43bf1 | Remote resource status currently unavailable |
| 62 | active | P1 | open | high | fee3a2f77e25dcba833e6242976a0b8a | Remote SSH banner exchange timed out during retained evidence recovery |
| 63 | active | P1 | open | high | 319480dd089a1225a40ad8a602838165 | Learning host apply blocked by remote SSH timeout |
| 64 | active | P1 | open | high | dde35952937cff4f87cd77549d568878 | Remote job output cannot resolve Sandbox home over SSH |
| 65 | active | P1 | open | high | b9815b2565193466965db85343ad26c2 | Repeated silent remote status probes block direct-host acceptance observation |
| 66 | active | P1 | open | high | ed693f7259cc768dd29fb4a01da89d48 | Detached remote exec returned no job result for standalone evaluator state |
| 67 | active | P1 | open | high | 1a7dde8bc4c88c8f7f311bf11c5ab5f3 | Remote active job list can return a false empty page |
| 68 | active | P1 | open | high | f7e83e9e0dc00d0c08591d45f7eca1f5 | Deploy cannot reconcile diverged managed remote branch |
| 69 | active | P1 | open | high | dcb1070b7240de1233c7c509a6d54c49 | Remote network pressure remains high at 31 active managed networks |
| 70 | active | P1 | open | high | d1a8a9f50d980bc8b1be061ef20c52ae | Remote deploy push timeout reproduced |
| 71 | active | P1 | open | high | 1ad96d2d24f9e1aa0bc401dde35eb2f1 | Remote deploy Git push timed out |
| 72 | active | P1 | open | high | c841bc711c53936d6a6ce94cfe7200e7 | Remote control updated; network pressure remains high |
| 73 | active | P1 | open | high | f1a869ebbf96377e09fb5f3dcee91c4c | Remote deploy git push hardcodes a 120s timeout; large repos can never deploy |
| 74 | active | P1 | open | high | 600d2def1a44dde2e811a79e01b9aa25 | Remote network pressure remains high at 31 active managed networks |
| 75 | active | P1 | open | high | 9db5f6426342bffb1befd24f3a8c0d05 | T105 r4 remote compatibility probe still cannot list durable jobs |
| 76 | active | P1 | open | high | 505bba6bf0a08db3d0e5762caa97bd7e | Remote job-start is blocked by project-identity protocol version skew |
| 77 | active | P1 | open | high | 234acaf1e04881a87ec78b47555f6d1b | Remote job-list client and direct host disagree on project-identity argument |
| 78 | active | P1 | open | high | 12e2ef47166e8deaa1cf5bbc698110d1 | Remote retained-output cursor read fails for accepted durable job |
| 79 | active | P1 | open | high | e5f1e6468abd63f375058b002020964a | Detached resource scan submission rejected by remote policy wire validation |
| 80 | active | P1 | open | high | fe5c3e02c95f5df8a1abfbda61ce9aff | Remote normalization retry hit acceptance transaction expiry |
| 81 | active | P1 | open | high | cdb2e1844f9557c118b44f9d36d46f71 | Remote provision leaves obsolete cloudflared after successful completion |
| 82 | active | P1 | open | high | 6f0b87cd91d110adcdd8e94c1037458d | Remote detached exec fails with supervisor_launch_failed |
| 83 | active | P1 | open | high | b8fcedf19c65046b16ecd8d61877ad6c | Confirmed remote provision returned no receipt and service health timed out |
| 84 | active | P1 | open | high | 641481fd3eb621604ab8caf7adeb01e8 | Remote resource measurement unavailable after CLI 0.2.2 update |
| 85 | active | P1 | open | high | 9bb7aea1a679db7233e18f8fbf31c841 | T120 evaluator full log and remote benchmark workflow gaps |
| 86 | active | P1 | open | high | 968d8f7a4416f26099ca49bff0b61b0b | Remote T120 bootstrap blocked by exhausted Docker subnet pools |
| 87 | active | P1 | open | high | 20c0829c9145fb5a0ead2849d13e258b | Remote preview project mapping breaks after exact source sync |
| 88 | active | P1 | open | high | a941c144b268fcb1f090deb46b75255d | Local E2E fixture became unreachable during Playwright |
| 89 | active | P1 | open | high | ac945dffe6fc473d30ee91368e301442 | Deploy wrapper accepted an unobservable job after SSH timeout |
| 90 | active | P1 | open | high | ab89060b777e09db660cec5cb2f40ba1 | Detached job submission hangs and exact workspace job-list returns empty object |
| 91 | active | P1 | open | high | f599f5f468f6a9c45ed83dd04b4b363b | Accepted T105 r6 job-status returned no payload |
| 92 | active | P1 | open | high | ab65eeee5a3c52338cebf41cc0e6361d | T105 r5 job-list preflight still fails despite reported runtime alignment |
| 93 | active | P1 | open | high | 52b78220ec4255f8d680b707ca409980 | Exact-head xSpeed PR review plugin activation fails in disposable Sandbox |
| 94 | active | P1 | open | high | 9290217a7686140b82bdad36fa84fb55 | Detached deploy output follow hung during process identity inspection |
| 95 | active | P1 | open | high | 3c4f9059a8509b919774072acb8aa63d | Direct-host T126 job-start returned no acceptance payload |
| 96 | active | P1 | open | high | 38efeba54f4a86c202a045b7cc55020a | Direct-host T124 job-start returned no acceptance payload |
| 97 | active | P1 | open | high | d7e6cba07d96a9d0d7af2d993b547640 | Direct-host job-start completed without acceptance payload |
| 98 | active | P1 | open | high | 388324ebc557a5eb27571b62ef26689c | Repeated learning host apply SSH timeout |
| 99 | active | P1 | open | high | eddb34e41036f7cf38f01ad0a1fc64fc | Repeated T105 r7 status poll returned no payload |
| 100 | active | P1 | open | high | 05605f7f730e2340d2787b05445da98d | T105 r7 status poll completed without a payload |
| 101 | active | P1 | open | high | d1a8b56d500abb30aa94cb671c4cea64 | Accepted T105 r6 job output read failed |
| 102 | active | P1 | open | high | 01f10c6bdbb113f102f4d1583b2d3e93 | Authorized corrected T105 job-start again returned no acceptance identity |
| 103 | active | P1 | open | high | 7f392acceefd15b24d8b513e3825c87e | Fresh T105 job-start returned no durable acceptance identity |
| 104 | active | P1 | open | high | fc8b79070969844dba5548dc0b1fcdca | Learning host apply stops at unhealthy background chat service |
| 105 | active | P1 | open | high | 9e9e30aa23570f8d7f50438d155b4cf9 | Fresh Sandbox cannot activate exact-head xSpeed PR source |
| 106 | active | P1 | open | high | 6cb71d7e0f95307d343ed6d8285357a7 | sb ensure site bootstrap times out on WordPress core version check |
| 107 | active | P1 | open | high | db34582b727bfea7d94ba446b681b9cc | Ox Alpha returned empty content for Lenzora implementation session |
| 108 | active | P1 | open | high | cd7025da758c2f5dc564dcc320748195 | Ox Alpha autonomous Sandbox worker stopped after repeated empty model responses |
| 109 | active | P1 | open | high | 120ce07b624da3c31e6c78f492adc04e | Hermes dashboard cannot resume saved TUI sessions |
| 110 | active | P1 | open | high | 7ef8c643198daefd31eaa66d7438562a | Hermes public dashboard exposure blocked by obsolete cloudflared |
| 111 | active | P1 | open | high | 2f6f1ecd91a543023a79414afdf8f939 | Hermes setup fails on fresh v0.18.2 profile creation |
| 112 | active | P1 | open | high | 5021b7b9e9d71c82c3e9eacb026ca963 | Agent worktree setup switched the shared root branch |
| 113 | active | P1 | open | high | 0dc659545d6568afe931b2cfe93669f8 | Sandbox feature branch push rejected by GitHub SSH |
| 114 | active | P1 | open | high | 3321034abad0e4834919d73f8531f3d8 | T120 evaluator report attachment relocated to user feedback storage |
| 115 | active | P1 | open | high | dc88af801eb6ddafe66aaea5c7d4f0c0 | Terminal output_bytes disagrees with complete retained output |
| 116 | active | P1 | open | high | 9ff08b134474af5bfc4e85cfad35ab9b | Preview root remains unresolvable with original config |
| 117 | active | P1 | open | high | fc1bc64c301c8dfe80b2c966e8149f20 | OrbStack stop interrupted Sandbox E2E and teardown |
| 118 | active | P1 | open | high | 0a5987c9daee5c691b28ed6573c1c68e | T105 r7 retained output long-poll failed |
| 119 | active | P1 | open | medium | ef04757903cd687e8b2f90bac15501e7 | No CLI path to install/run wp-cli against an existing remote preview instance |
| 120 | active | P1 | open | medium | af8a2aae8503aedb52d1c02c80d3c55a | Remote status cannot inspect an existing deployed instance without a prepared workspace |
| 121 | active | P1 | open | medium | 4947b7763ee36b4f7d0fb623f3349b80 | Remote status cannot inspect an existing deployed instance without a prepared workspace |
| 122 | active | P1 | open | medium | 381ddbc764cadd387082bf9ce4699a13 | Resource scan workspace status is blocked by remote revision mismatch |
| 123 | active | P1 | open | medium | 5f0ab2b1327aa41ee8d0ed9d1c62cd82 | Remote Sandbox workspace inventory blocked by runtime revision mismatch |
| 124 | active | P1 | open | medium | 2a58b1355f744e47c292bad84af085e8 | Remote status rejects read-only PR feasibility when no project instance exists |
| 125 | active | P1 | open | medium | a08b008462a83e6fb5a3c3263e8d7e07 | Remote fast resource status times out with capacity-only evidence |
| 126 | active | P1 | open | medium | cc723c158ba1e9c9d493f7844ae3e690 | Hosted apply lacks a read-only post-deploy status and revision action |
| 127 | active | P1 | open | medium | 46761ea6ddf4f6512f1cec0ae078a5eb | Remote resource status returns transport timeout despite capacity result |
| 128 | active | P1 | open | medium | 74e77a060d3b5d9c230cb8a2b125b3a3 | Remote workspace list blocked by runtime revision mismatch |
| 129 | active | P1 | open | medium | f6d87b725c8b1985429353b816955aed | sb debug test: remote deploy git push times out after 120s (templately unit suite) |
| 130 | active | P1 | open | medium | c2490b7c083289421901fb49ad9a3a1e | Remote resource status still times out on deployment retry |
| 131 | active | P1 | open | medium | be7f222d6f7e9d7a9ea3dc4a9168c63b | Remote resource status times out before host deployment |
| 132 | active | P1 | open | medium | b3905bbe81f9b2955cc7f5aa74dd6f0a | Remote direct-host job status SSH control timed out |
| 133 | active | P1 | open | medium | e4ae52d8cc3be928ceb442e7a641e50a | Remote job-status returned no output for active direct-host job |
| 134 | active | P1 | open | medium | 0aee03170ca08b02e3c46a7ecf666e69 | Remote job output read failed while active job status succeeded |
| 135 | active | P1 | open | medium | 5fc4d97c873e6e3207b7d0452d782289 | Remote status cannot map local worktree instance |
| 136 | active | P1 | open | medium | 8b88c87e231e7eedc19d9410289688ca | job-status returned no output after accepted remote job |
| 137 | active | P1 | open | medium | 89e64f460d9d911ed95002af3a79e7d9 | Remote workspace list is blocked by revision mismatch |
| 138 | active | P1 | open | medium | 0c10084f7c35220aa1f8028c68790ae4 | Remote unit tests depend on an unbuilt MCP venv |
| 139 | active | P1 | open | medium | ed93e4bcd014ae83495d902df6d2a0af | Detached remote resources status has no durable CLI mode; ordinary background child was reaped by the command runner |
| 140 | active | P1 | open | medium | ac8b53198f58568c95045c115ff09824 | Remote Sandbox deploy rejects detached exact-head review worktree |
| 141 | active | P1 | open | medium | de0c16c505c26abdc44ecb279b0a0df9 | Remote exact-tree deployment rejects detached review worktrees |
| 142 | active | P1 | open | medium | 2e3b4d35178004728037fcffe49d8d16 | remote sb exec returns accepted job without retrievable completion |
| 143 | active | P1 | open | medium | 663e516749ac7822692274cbcc6e1cc5 | Remote resource status fast probe timed out |
| 144 | active | P1 | open | medium | 424a74fbe61106a4d89f65a55f82dcff | Remote exec selected a local durable job and timed out |
| 145 | active | P1 | open | medium | 2fe446c36654412cca97de2be7105690 | Hosted apply rejects an email-valued configuration entry as a secret source name |
| 146 | active | P1 | open | medium | f224aadf783352b84eb0159c7f8a9360 | Hermes provider status cannot inspect an already configured remote OpenRouter provider |
| 147 | active | P1 | open | medium | 99d69b660d4e1160f6c70d6b867619b7 | Remote workspace inventory blocked by revision mismatch |
| 148 | active | P1 | open | medium | a55cec510b3714111cc3cd9331438bab | Remote job-output wait limit conflicts with documented 60 seconds |
| 149 | active | P1 | open | medium | f294f2948147e7248c65714d0ee0f8af | Remote workspace logs do not resolve named workspace |
| 150 | active | P1 | open | medium | 1ef4334de71d0a937609d2b772520a82 | Hosted deploy status is not inspectable through host command |
| 151 | active | P1 | open | medium | b41513e955d92bf2a1c408f381d21af2 | Hosted deployment has no status or revision inspection command |
| 152 | active | P1 | open | medium | 9a75afb42fad55346167b36c6312acaf | Remote resources fast status timed out before attribution |
| 153 | active | P1 | open | medium | 843b42f7fc46db8196117fea019fa6c9 | Local labeled E2E unexpectedly used remote capacity admission |
| 154 | active | P1 | open | medium | b2177d0ed8b796f1ff0e990e701d5f0c | Fast remote resource status timed out without its promised cached index |
| 155 | active | P1 | open | medium | 560976950cfd30d5ce8737f9e6df95a8 | Instances command has no remote inventory selector |
| 156 | active | P1 | open | medium | 72022a6a81166cbd17baae6b88b92839 | Add a deterministic remote test bootstrap profile and phase-aware evidence output |
| 157 | active | P1 | open | medium | 93bbb08ed676e92604a4f29726ce1aca | Remote resource status returns partial coverage with timed-out categories |
| 158 | active | P1 | open | medium | 630d251dd4fc61d4f0f4d84d0557fd39 | Remote domain inventory misclassified nested Caddy directives as hostnames |
| 159 | active | P1 | open | medium | 6c9b438100cbf34b3474ab46d2f0dd8c | Remote output transport failure recurs during direct-host job |
| 160 | active | P1 | open | medium | ef18692dbbbabe7720bcb5afef2e0de1 | Detached remote exec returned no job identifier |
| 161 | active | P1 | open | medium | 092392ad9cc9b22ca3aa0ef3fa17f698 | Remote project resolution ignores matching registered instance |
| 162 | active | P1 | open | medium | 2a7f6e60382ff387a84ce5ea3112baf0 | Remote retained-output read fails for active direct-host job |
| 163 | active | P1 | open | medium | 92511a2c00128e5120683f5c0386f6ad | workspace create ignores explicit Scaleway remote target |
| 164 | active | P1 | open | medium | 2374342bd83e0e662a1ce8676c6d3f87 | Local remote selector listed nonexistent VPS instance |
| 165 | active | P1 | open | medium | fd8affd7d173d7314887800477c4fce4 | Remote job-output read failed for healthy retained job |
| 166 | active | P1 | open | medium | 2849f90e57e507f07cb218d168cc4058 | Remote instance error listed local registry |
| 167 | active | P1 | open | medium | 128f35781c4be662f8df875e7683e68d | accepted remote resource scan can remain queued without position after prior retained scan completes |
| 168 | active | P1 | open | medium | 7a349cf706b132bc63a1edbef1edcdda | Remote deep storage scan timed out before attribution |
| 169 | active | P1 | open | medium | 9af5c990a7efde8bf2a61d1c8ee31cf7 | Remote storage attribution scan timed out with large unknown bucket |
| 170 | active | P1 | open | medium | 2d168956422b2f6aeee4ef754bc63b95 | remote exec accepted id cannot be queried |
| 171 | active | P1 | open | medium | b6e84616c1ed7e21a41136c604a00602 | Hermes host run cannot bootstrap from a private repository without remote GitHub auth |
| 172 | active | P1 | open | medium | e2cffada572053c0b41500419c79dc40 | Hermes run requires a managed repo despite supporting remote operations |
| 173 | active | P1 | open | medium | 43f98577513e43026ffdf03d1c6481e8 | Hermes health remains degraded although remote MCP is healthy |
| 174 | active | P1 | open | medium | e3d8c553b3b24bafeaddde245294ddaf | Thorough remote resource inventory remains partial at 60 seconds |
| 175 | active | P1 | open | medium | bc1f0f8974b9dd55e241f1f257572530 | reveal-login refused remote ensure records, leaving E2E runners with a placeholder login URL |
| 176 | active | P1 | open | medium | 6f513cf08218e309dcdc650ae151c94e | Remote Sandbox guide rejects documented local flag |
| 177 | active | P1 | open | medium | 56bf50f61cf5065710dc5acd608575fa | Remote host operation lacks a brokered exec path |
| 178 | active | P1 | open | medium | 453aef2840ed33dcffe10cc9c87855b6 | Remote exec rejects standalone evaluator state under /tmp |
| 179 | active | P1 | open | medium | 704a866370b6c283352e21862e7b7040 | Documented remote Sandbox path is not valid |
| 180 | active | P1 | open | medium | 3727d6d5ba84090a5c1a5dbbe1e408ee | Doctor crashed before remote diagnostics during protocol-skew investigation |
| 181 | active | P1 | open | medium | db90e71e7e51c4dcbad486e60c91a627 | sb test and sb apply refuse to work in a detached-HEAD worktree, which is the normal shape for PR review |
| 182 | active | P1 | open | medium | a45de86b853fe15cc089f5799ebcc98b | preview create does not report the instance name or runtime dir it created, so follow-up work has to guess by glob |
| 183 | active | P1 | open | medium | 871ee1a12b74738e7885fea98472d654 | job-output cannot retrieve the live metrics stream that job-status reports |
| 184 | active | P1 | open | medium | 9db422b1be73c60e0e0f5f5f7834de81 | Focused E2E defaults WP-CLI to nonexistent /tmp/wp despite registered Sandbox instance |
| 185 | active | P1 | open | medium | 9fd4afbcf6bd5efbf7698d9906f02169 | Secret runner reports redaction failure for a successful non-sensitive status probe |
| 186 | active | P1 | open | medium | b89fe5e677f3b94ba9105e39bffaad27 | Job CLI matrix regression is not isolated from persistent workspace bindings |
| 187 | active | P1 | open | medium | f535fa1357d42bf2a8dd30737af37fbe | job-status rejects intuitive --job-id flag |
| 188 | active | P1 | open | medium | 20cc2f0a53ab4bc421f46735c1742cf1 | Sandbox stale WordPress instance cannot be reconciled when database service is missing |
| 189 | active | P1 | open | medium | 895342edf9b7a8f5b65515a16aed15d7 | Generic Compose deployment rejects valid dotted project directory as WordPress slug |
| 190 | active | P1 | open | medium | d46a9bb76aa1733ec0f933b278a83c57 | web CSS builder no longer finds modular page shell |
| 191 | active | P1 | open | medium | ac760c7b02d4b81fe697c6cd15c6001b | Active broken plugin blocks wp plugin replacement recovery |
| 192 | active | P1 | open | medium | a72307fa19c6d89ec6f8c1cdc901a8b4 | ensure returned empty output without creating review instance |
| 193 | active | P1 | open | medium | c80f91e0fd2bdb79555d89cb0920c5cd | Exact-head xspeed activation fails when vendor autoload is absent |
| 194 | active | P1 | open | medium | 7d88045f9c8e92c3fdeded2754a837bc | Applying a valid xspeed sandbox config failed on unresolved inherited plugin |
| 195 | active | P1 | open | medium | 5fec1a2ad96785ac922e45d1078915a7 | Dev push deploy hook uses wrong Node version |
| 196 | active | P1 | open | medium | fb21264967c998f7bc20368e961bcc3b | Sandbox CLI deploy fails when pyenv selects unavailable python3.13 |
| 197 | active | P1 | open | medium | 2c76137a00c63a97e40ff2086dfe89fb | Post-merge Lenzora deploy hook ignores the repository Node version |
| 198 | active | P1 | open | medium | 56b29b3604b6f4ae1fdee8bef3334daa | Post-merge dev deployment does not bootstrap the required Node version |
| 199 | active | P1 | open | medium | a3050df7119b8750a6da00837951d014 | Hermes configured status hides absent dashboard |
| 200 | active | P1 | open | medium | f81f8349985787c6825f0013e0866328 | Host logs discovery timed out after Docker test workload |
| 201 | active | P1 | open | medium | 2a918e7c8aec57b786fcbf40bd9e0a64 | Post-merge Sandbox deploy hook ignores the project Node pin |
| 202 | active | P1 | open | medium | d14342c79c4a55b077ab3d5a368e589d | sb ensure changes explicit instance name and omits requested JSON result |
| 203 | active | P1 | open | medium | 9898984862bc034e50fda397d85afa3f | sb doctor detects a dead proxy port-forward but offers no repair; docker restart sandbox-proxy fixes it |
| 204 | active | P1 | open | medium | 793c3d1b9b951af0f00424f63a91b554 | job-start help does not enumerate valid execution profiles |
| 205 | active | P1 | open | medium | 6944e3417b63aecd7cc2dd3f687993c9 | Direct-host job preserved early assertion failure as terminal evidence |
| 206 | active | P1 | open | medium | 39f83b4daec5c90af74a4125b1329c3e | Read-only jobs lookup cannot resolve isolated registered-project checkout |
| 207 | active | P1 | open | medium | 718e64637a1a5462c92a11af6a092911 | Batched corrected-job evidence reads timed out at SSH control |
| 208 | active | P1 | open | medium | a1de4edbe535da9e5ecf4ee4780569f1 | Direct-host job output read failed while job active |
| 209 | active | P1 | open | medium | 621aeec140a2f92f93b5ab2ccaa11acb | Deploy rejects safe detached deployment snapshot |
| 210 | active | P1 | open | medium | 499afa9e8fc6a10a625821e2bede72a0 | direct job-start accepted sh wrapper with bash-only pipefail |
| 211 | active | P1 | open | medium | 4ae20bf73cf57308ece0a8c79a21ad41 | Runtime guide references missing project Sandbox launcher |
| 212 | active | P1 | open | medium | 7032764f3afc12d1ac55d767c4f18210 | Fresh HSSB WordPress 7.1 E2E provisioning exhausted Docker network pools |
| 213 | active | P1 | open | medium | e3c10123ab75ea297d49e7aa1d014794 | WordPress runtime cannot execute wp db query because mysql client is missing |
| 214 | active | P1 | open | medium | 96819c8e948b59b8205afb53d5383041 | Hermes repo sync cannot refresh provisioned Sandbox runtime |
| 215 | active | P1 | open | medium | 37760be8415b0a79aea9e5775d00a7ea | Hosting command group has no discoverable hosting skill |
| 216 | active | P1 | open | medium | 34b7e8f69a6ded4368b37fddf8fcf8ed | Repeated sb wp startup exhausts Playwright setup timeout |
| 217 | active | P1 | open | medium | 5b79bab6434c746598accf35e3c1c474 | Sandbox ensure rejects disposable PR review clone under /tmp |
| 218 | active | P1 | open | medium | 03f51d2fce72cca73cc499b7d3d30e99 | Free PR checkout cannot be initialized for Sandbox without compose config |
| 219 | active | P1 | open | medium | 941dacfc126a3e5087fc1783d21df829 | Repeated linked-worktree Node and dependency bootstrap toil |
| 220 | active | P1 | open | medium | 34088c3da0522f16db1a000046518234 | Herd ability execution probe fatals and wp wrapper prompts noninteractively |
| 221 | active | P1 | open | medium | eb496b17c1c33e47aaa7e74fde5ea4bf | Feedback JSON list crashes when since filter is used |
| 222 | active | P1 | open | medium | feacbc91b1d5e2c6c3056ca78dc90eca | Ensure does not recover database after interrupted first provisioning |
| 223 | active | P1 | open | low | 5ce268c6b6c7e9d8e5cc79312ddfd961 | Remote network-capacity monitor: no managed pool pressure |
| 224 | active | P1 | open | low | 1e799cd8c24fc081924a8ed99b369fab | Remote network-capacity monitor: no managed pool pressure |
| 225 | active | P1 | open | low | 1f4372da2337ec163743ffc8af70035c | Remote capacity monitor recovered after runtime refresh |
| 226 | active | P1 | open | low | b94b737c8813c0fa613d4b4a8f969d26 | Remote network-capacity monitor: ownership incomplete |
| 227 | active | P1 | open | low | 37643f63e567d44d4fa153062559b911 | Remote network monitor: ownership remains incomplete |
| 228 | active | P1 | open | low | 756a8df6696e71385fa15134a9992308 | Remote network monitor recovered with incomplete ownership |
| 229 | active | P1 | open | low | 80b4dea2b687f3ab4c2e3c807bddf34b | Read-only remote resource probe could not measure host capacity |
| 230 | active | P1 | open | low | 66cca31e474b70b2260eaa41a6dcd317 | Remote resource scan partial despite low network pressure |
| 231 | active | P1 | open | low | 7a03bd1d37303521466987c81337c254 | Remote network pool pressure classified low |
| 232 | active | P1 | open | low | dd4f8d96d5e8794d97b21bdc0e68d07a | Remote resource preflight unavailable during feedback triage |
| 233 | active | P1 | open | low | 3bab1363b29e8a2225e1e760fbdbace2 | Support-floor smoke assertion failed in WP-CLI runtime |
| 234 | active | P1 | open | low | 2ad74188c8f1636c572542e4f1e5408b | Support-floor content equality assertion failed again |
| 235 | active | P1 | unreviewed | high | fd48198f00b9c48fccaac8cee6bdf84a | Lenzora development host apply blocked by repeated SSH banner timeout |
| 236 | active | P1 | unreviewed | high | 49ea2ef5f18112ea22e5f9ce07686b23 | Main-branch remote Sandbox runtime migration blocked by SSH banner timeout |
| 237 | active | P1 | unreviewed | high | 6d89bec0395e060774bb4134080be298 | Remote deep resource scan cannot measure host capacity for container cleanup |
| 238 | active | P1 | unreviewed | high | 71c12b23c43db92dcaacc9d1d1cb71ab | Remote workspace cleanup returns indeterminate after a completed safe reap |
| 239 | active | P2 | open | medium | dbb7068f992c1aca082904ce3ef7fa68 | Allow a container to invoke an allowlisted command on the HOST |
| 240 | active | P2 | open | medium | 5d79047c59d6194588cdab4eae846b00 | Disposable WordPress worktree instance omits reviewed plugin and cannot install project ZIP |
| 241 | active | P2 | open | medium | 56551456d608b92220dcf52388cbb6f6 | sb wp plugin install cannot bridge an absolute host ZIP path |
| 242 | active | P2 | open | medium | 711940db2d0c9bbce9ad5130444cc3f9 | Disposable plugin mapping activates before vendor dependencies are installed |
| 243 | active | P2 | open | medium | fdd88ab7c3b78f3e1b0e694db6c50b0a | pyenv pin 3.12 missing on machine: every sb invocation in sandbox repo fails before argparse |
| 244 | active | P2 | open | medium | 92966e707ba418e055820f9e3c71f1e4 | sb init did not install or upgrade the configured WordPress project |
| 245 | active | P2 | open | medium | 5393e210a5f22a86af9b5ad6a409f896 | sb test --local --label does not create a missing isolated test instance |
| 246 | active | P2 | open | medium | 4ea48c7a73fa466a22fc6a13c0ce9134 | PR review temp worktree rejected by WordPress instance resolver |
| 247 | active | P2 | open | medium | 2cfab06ff5393a6e759df277fa818dca | secrets run 1800s cap blocks running a long-lived dev server under a brokered secret |
| 248 | active | P2 | open | medium | 6ae07ae76205e45d8fd6782bee16b212 | Secret-run agent launch surfaced OpenRouter SDK maxPrice schema mismatch |
| 249 | active | P2 | open | medium | 18c1ac3d3b7821087cc4d6a26bbf632a | Secret broker lacks OpenRouter API key validation profile |
| 250 | active | P2 | open | medium | 133d878c1f52ca34065d514ee72c76d9 | Registered WordPress project lost its Sandbox instance |
| 251 | active | P2 | open | medium | 20a2508436ae6be32d8da8ec065ebda6 | Fresh local Sandbox instance loses project association between commands |
| 252 | active | P2 | open | medium | 6ef03d44b29cf10c87f56f6634e68ca3 | Full unittest discovery aborts: a test invokes the sb parser with the discovery argv |
| 253 | active | P2 | open | medium | d89c5644b44dc5c7159d6164d62a815d | sb secrets has no delete/unset operation, so stale keys cannot be removed through sanctioned tooling |
| 254 | active | P2 | open | medium | 7e6374beb19ef30884e78745a4a87281 | Known xSpeed instance name and label guidance conflict |
| 255 | active | P2 | open | medium | 3f0bc71ac86f145ab9480f5972800a63 | Plugin Check exact ZIP workflow lacks a clear Sandbox path |
| 256 | active | P2 | open | medium | b2ed6f764e1f61cdacb5b03f32f5e986 | wp eval namespaced cache toggle quoting remains brittle |
| 257 | active | P2 | open | medium | eb0e3845185bfe348d35ac34a6a156bf | wp eval rejects valid namespaced PHP reflection command |
| 258 | active | P2 | open | medium | cabb0469159a2357c994c84893fa8c44 | sb host validate requires an undocumented environment selector for multi-environment manifests |
| 259 | active | P2 | open | medium | 89b94c60c3470010af34126a3f671dc1 | Plugin ZIP aborts on Hamcrest generator template |
| 260 | active | P2 | open | medium | 171f639a9f4dcd7bea3f502dd6c00a49 | Disposable review worktrees under system temp are rejected |
| 261 | active | P2 | open | medium | 82ec5d6e863604d66959a64490e07853 | Sandbox WordPress wp command rejects documented project-dir routing |
| 262 | active | P2 | open | medium | 54ec837a1a5260180308720f43dd8763 | Sandbox rejects valid detached worktree because generated directory basename is not a plugin slug |
| 263 | active | P2 | open | medium | 7f75fabc5156991f6c33f9bcb2a7caf4 | Disposable project init fails on inherited unresolved optional plugins |
| 264 | active | P2 | open | medium | c8c82eb12b63fbc8bdde2afc04d909c4 | Deep storage scan completes only with partial inner attribution |
| 265 | active | P2 | open | medium | 5b07febc49a9eca2944669bb86ca4fb2 | Sandbox isolated worktree setup rejects valid review paths and service targeting is unclear |
| 266 | active | P2 | open | medium | e8ddb411eb6d706b2959e96ade8e86e8 | feedback list pagination emitted invalid JSON control characters |
| 267 | active | P2 | open | medium | 028148a48ab7f607d8a32a6ea789fb92 | Hermes public repository clone returns clone_failed during normal checkout |
| 268 | active | P2 | open | medium | cd9baf02fa2727215178513c323a2ca8 | Hermes repo sync reports missing managed repository for Sandbox |
| 269 | active | P2 | open | medium | 39d1950882d21545dfa0599f44d9f7b7 | Hermes iteration-cap controller used config get unsupported by pinned Hermes v0.18.2 |
| 270 | active | P2 | open | medium | 1ff68cf292a169877840db44823f90b0 | Sandbox launcher ignores bundled CLI virtualenv when pyenv 3.12 is missing |
| 271 | active | P2 | open | medium | d47f53dccc7fce3cbf314211a0932219 | Hermes shared external skill scan omits design-fidelity-diff without a verdict |
| 272 | active | P2 | open | medium | b1864a6bfe983b15f24a26f7c20a88b2 | Hermes skill registration needs an explicit discovery reload |
| 273 | active | P2 | open | medium | ee7fa861f1ca6b76b87fc3bab53a84c0 | Hermes public-route adoption cannot resolve Access policies from list response |
| 274 | active | P2 | open | medium | 7a2cdddbf791a51423e0a713e98997a5 | Hermes OpenRouter setup used unsupported config read command |
| 275 | active | P2 | open | medium | 9680116c0afdaee8448f2e68421710b3 | Hermes OpenRouter setup command misses os import |
| 276 | active | P2 | open | medium | d619a2a7c33c4e21073928e29eeef266 | Scoped WordPress core update failed after unpacking version 7.1 |
| 277 | active | P2 | open | medium | 6687abd93e5d8ca4f8d5d6a56fb7c8fa | CI preflight blocks full Lenzora workflow on Sandbox act runner |
| 278 | active | P2 | open | medium | 103ae36f12548a7680c414bff2043999 | Sandbox WordPress plugin path contradicts plugin list |
| 279 | active | P2 | open | medium | 4e7e5f254889298791b77e8ef05d56d8 | Sandbox CLI discoverability gap: no  subcommand |
| 280 | active | P2 | open | medium | c7ea6da8687db97c2dbba94c24316e5a | Guide documents request-id for detached exec but CLI rejects it |
| 281 | active | P2 | open | medium | 93824bd2ad4bcd66c6628edda9c498dd | doctor rejects standard local project routing flags |
| 282 | active | P2 | open | medium | e0a9c65980b8f16b2febf7e74f27e649 | sb exec rejects documented request-id option |
| 283 | active | P2 | open | medium | cd1f7a9858b8e6077c2a7ed8d57b4fff | Fresh Sandbox worktree guide fails when incomplete CLI virtualenv exists |
| 284 | active | P2 | open | medium | 74d503ab3bef8d3392432e2232760a93 | Full Sandbox suite retains three baseline failures |
| 285 | active | P2 | open | medium | 423e3e912f87cbf0024837d275a807dd | Sandbox logs does not provide bounded terminal snapshot |
| 286 | active | P2 | open | medium | e341ae5ea348d7f62794117b309a8ee5 | Spec Kit precheck cannot target amended existing specs |
| 287 | active | P2 | open | medium | 1034d4ca80006f016554337db1264aae | Fresh ZIP-only fixture contains destination plugin directory |
| 288 | active | P2 | open | medium | 6496ba60bf80c6cff680d2e95384f23d | Sandbox E2E wrapper hides Playwright failure output after successful provisioning |
| 289 | active | P2 | open | medium | e3dadfcd00c26989ad4590947f6d1b80 | WP media import does not map project file and pollutes porcelain capture |
| 290 | active | P2 | open | medium | 6240dc67560ab8cd7ce2188a2c657ab0 | Plugin Check adapter rejects successful output |
| 291 | active | P2 | open | medium | b32a5250c5a22045c95059da67bcb10c | E2E wrapper omits Playwright failure detail |
| 292 | active | P2 | open | medium | 25c004af5c238bc706b84ac833594aab | Plugin Check wrapper rejects successful no-error output |
| 293 | active | P2 | open | medium | bb1f932babd2e4e0c1afebe0621456ee | Restore confirmation flag is not exposed by the CLI parser |
| 294 | active | P2 | open | medium | 5d9f1c65067dd7cf0079220a4ebc2060 | Host plan omits declared background services |
| 295 | active | P2 | open | low | c73e13c16883f1ccc416df26f59fb08c | Remote service subcommand usage is not discoverable from remote --help |
| 296 | active | P2 | open | low | d4a37b6f22edeee8563d7abd943714c4 | Host validate cannot validate every manifest environment in one call |
| 297 | active | P2 | open | low | 7e6bf32d6bb2a0207fcd612f8d46503e | Remote status requires non-obvious instance selection from isolated worktree |
| 298 | active | P2 | open | low | a5af1834c48ec3b70080840b061068e1 | Correction: deploy job ledger is local, not remote |
| 299 | active | P2 | open | low | 74212a8043f83fa50446b94e2f76ccb7 | remote service migrate returned no CLI output despite successful apply |
| 300 | active | P2 | open | low | f528a47206c7b9d0f1d36dd17ee7def9 | Hosted deployment status cannot resolve deploy-source project directory |
| 301 | active | P2 | open | low | f200d37deea45ebc6375d5e519302b64 | Remote status command is not discoverable |
| 302 | active | P2 | open | low | 763fbc6ea330e48be19fad5a4f54eaac | job-output wait bound differs between local CLI and remote service |
| 303 | active | P2 | open | low | 8d5b6bce700d8ef592e8c4d18cb59248 | shell WP-CLI diagnostics need explicit root allowance |
| 304 | active | P2 | open | low | 2f2699256f50fcc2520d245b0c2aa8a3 | sb e2e reached Playwright but could not resolve @playwright/test from the project |
| 305 | active | P2 | open | low | bd617aa4a6c91dafef6ecca7700243ad | Host plan requires explicit remote after successful manifest validation |
| 306 | active | P2 | open | low | 1440ad3df0c3d1d28c55ec5129a8298e | Sandbox secret inspection fails under the default pyenv selector even though Python 3.13.1 is installed |
| 307 | active | P2 | open | low | 6bf36b944cf8846af3cfcc33279555d3 | WP plugin list rejected undocumented path field during remote inspection |
| 308 | active | P2 | open | low | 7c93654b14d054d504caf2b40b2c426e | Evaluator local path glob was not resolved before remote submission |
| 309 | active | P2 | open | low | 29f0128acae302ae957950feaada51fb | Global sb exec rejects project-dir although workspace accepts it |
| 310 | active | P2 | open | low | 5bda94d762e7ecea4c82d3060d6b8b19 | Remote list rejects project-dir selector |
| 311 | active | P2 | open | low | 27a3ebac6330510ef46400ea4a8fbbc9 | Agent runner syntax check used caller cwd instead of runner directory |
| 312 | active | P2 | open | low | 7ea39dad34d9d23bd530ad7be14f6717 | Hosted log tailing is not discoverable and rejects the common --tail argument |
| 313 | active | P2 | open | low | 3ce28a2c9f32e370ec93183f2d818d78 | Hermes doctor treats expected pre-install absence as failure |
| 314 | active | P2 | open | low | ca6c46b208c40d2a16cefe0d7fe5c827 | Project-scoped status should explain missing instance context |
| 315 | active | P2 | open | low | 301231455c3fca6af640704a630de8cb | Project-mounted WP-CLI paths and E2E cleanup need clearer reporting |
| 316 | active | P2 | open | low | ac14f1c22e465cd6127e6846fb509fe4 | ensure recovery rejects the instance name returned by ensure create |
| 317 | active | P2 | open | low | ed61d1e45f5aac0b41a762f3b7dabcc5 | Guide status command fails before first instance creation |
| 318 | active | P2 | open | low | 64bcf5e5c6ece82d395a5a5a2ebc87f9 | Status cannot inspect unregistered project without an instance |
| 319 | active | P2 | open | low | f372b16887dd99be160dab62859c2fd3 | Instance deletion preserved unreceipted legacy domain artifacts |
| 320 | active | P2 | open | low | 135e0014fd2946ecda09abbde70fed8a | Instance deletion preserves unreceipted legacy domain artifacts |
| 321 | active | P2 | open | low | c50b7e5f00cc9c57f51d66a93f8cf07a | test_modularity baseline runtime_kind_branches is stale (111 vs 115 at HEAD) |
| 322 | active | P2 | open | low | 319295b97505c5105698076a71ca2431 | Local status requires a registered instance |
| 323 | active | P2 | open | low | 48369c862e31b1ed4d9a33817f0e014e | Instance status subcommand is unavailable |
| 324 | active | P2 | open | low | 4bbcf21ce7af47e77fad318f579a2692 | Theme status pass-through gives no guidance for invalid WP-CLI field form |
| 325 | active | P2 | open | low | 0ca4656a7af7c37503dbe50b27d466a5 | Brokered secret command succeeded but inline tsx evaluation failed on top-level await |
| 326 | active | P2 | open | low | 3fa2c05db19e2b541435e4c494eb9ceb | Focused unittest module name was missing |
| 327 | active | P2 | open | low | b1ad1d168ae2a184d0638da7a58f95fd | Nested sb wp rejects project-dir placement during secrets run |
| 328 | active | P2 | open | low | 3c575e3573915a8a9f12f3d8f3cd5147 | fresh ensure instance lacks Plugin Check despite project PHP validation workflow |
| 329 | active | P2 | open | low | bd27d2cbbfae7a8b8dd5fdee79450087 | sb wp passthru test command surfaced missing explicit phpunit.xml |
| 330 | active | P2 | open | low | fa6d5f85dfbf142e90381530730b250a | Sandbox wp cannot target an instance from detached review worktree cwd |
| 331 | active | P2 | open | low | 81cbcc2283df364ee7d3b6090b2587a4 | WP-CLI help through sb fails because less is missing |
| 332 | active | P2 | open | low | 6b22c6f6ced583e1b946a6d165598a0d | sb up does not support JSON output unlike status |
| 333 | active | P2 | open | low | 758ed630bd86503a3ecc7600019f4213 | Temporary PR review worktree cannot resolve existing W3TC instance |
| 334 | active | P2 | open | low | 1b6c9384b1bce1b4f7bf9e399dfbd571 | License status command rejects documented-style JSON flag |
| 335 | active | P2 | open | low | bdbd66546d3f0c01289a2c3c34dfb191 | Plugin deactivation reports batch failure for one absent slug after deactivating others |
| 336 | active | P2 | open | low | d02cc0ff45272ba13559ccab3ba05bcf | sb job-output rejects wait longer than its documented polling use |
| 337 | active | P2 | open | low | 55f213b00c715e051ac5d91c73bcba6e | Secret-run timeout ceiling is undocumented at agent launch |
| 338 | active | P2 | open | low | 72d7e41672b419a7ed688f56130e9f4f | secrets run rejects direct Node -e arguments |
| 339 | active | P2 | open | low | 312999f52f6275df463d3178d1c8cd00 | Secret key inventory flag differs from documented invocation |
| 340 | active | P2 | open | low | 4932ce6cb034428b9d058372f27b1384 | Focused Hermes test class is not discoverable |
| 341 | active | P2 | open | low | 26f42e8614d8782fd3247ad1812543a3 | Post-merge sandbox deploy hook ignores repository Node pin |
| 342 | active | P2 | open | low | d31d50a62fee3d648aa1a0943e5ed38e | Post-merge dev deploy hook ignores repository Node pin |
| 343 | active | P2 | open | low | 234ae3c23f48e079e884c3f6a42fdd97 | Execution guard rejects temporary test cleanup |
| 344 | active | P2 | open | low | 5d686f805c040875b70647936c3eb852 | Host status requires hidden environment selection |
| 345 | active | P2 | open | low | c7148951984491f103e0d106d190b7d5 | Focused test file selection is not discoverable from sb test |
| 346 | active | P2 | open | low | aff7c116c78be838405d39bdc6f8e502 | Focused Python test invocation used system Python 2.7 |
| 347 | active | P2 | open | low | b2b1372ef928343fdf27bb60cc0e2793 | WordPress project status omits actionable local bootstrap hint |
| 348 | active | P2 | open | low | fbdb7ee1c831849bdea6cfce516eec15 | Focused sb test surfaced test fixture constructor error |
| 349 | active | P2 | open | low | 2affb4b3ad518bb79fca14bfacbc29f8 | Unset optional WP option lookup propagates nonzero status |
| 350 | active | P2 | open | low | 8683c017651a8a817235498a8fdf0c5b | Spec implementation live-proof preflight lacks a registered instance |
| 351 | active | P2 | open | low | 5d1cf19beed3520de67e717ccc85dfb9 | New job acceptance flush regression missed io import |
| 352 | active | P2 | open | low | 8eee850065b3be5e0c9dfabb70a7babd | Ambient python3 invoked Sandbox CLI during focused unittest run |
| 353 | active | P2 | open | low | 95c4db6a7c6715abf72ccdcff5e68a85 | Local instance JSON flags do not return JSON |
| 354 | active | P2 | open | low | 32e67b5b5d127bed5e828e3cb7253f2d | Runtime guide advertises unavailable project-local sb path |
| 355 | active | P2 | open | low | 4aa1e5e9204dea64cfd9b821953fa75c | Top-level sb destroy guidance is inconsistent with the actual instance lifecycle command |
| 356 | active | P2 | open | low | 02f1514ddbb7e0510f30ec401834f045 | Focused Sandbox test correctly surfaced a project assertion failure |
| 357 | active | P2 | open | low | 14563ae5e075bcbf90b628efa8cb0cf2 | Test bootstrap failures are interleaved with successful provisioning output |
| 358 | active | P2 | open | low | 0ac8cb5dabd519d03ee0f27802bb51e3 | WordPress eval-file cannot resolve project-relative test path |
| 359 | active | P2 | open | low | a62ad5240b0fcd7a7016144f5de814b0 | Status JSON failure is emitted as plain text |
| 360 | active | P2 | open | low | 2b348f268c95d7335eda13a5166ba505 | Doctor audit result needs distinct diagnostic exit semantics |
| 361 | active | P2 | open | low | 947f752eeffe6c3220adab1e06a30394 | General research skill lookup failed through sb skill show |
| 362 | active | P2 | open | low | ab0866d29c12cd197a722f9563020bc0 | Sandbox compose logs option does not accept documented tail argument |
| 363 | active | P2 | open | low | c444755c51a1eed1d5f23f6a7e47bcc2 | Host validation requires undocumented environment flag for multi-environment manifest |
| 364 | active | P2 | open | low | 846518da5be863f7c75172fd85bcf451 | Exact ZIP Plugin Check requires archive extraction inside the WordPress container, but the container lacks unzip. |
| 365 | active | P2 | open | low | c025a7bccd06656ce1cfbaf618442431 | wp option get requires a no-error probe for missing settings |
| 366 | active | P2 | open | low | 0f197b2fc399d6e0b9fd028ec5bd5037 | wp eval diagnostics surface duplicate critical-error wrapper |
| 367 | active | P2 | open | low | 4d8151f05e3746744ba612c919f44897 | sb e2e does not preflight the Chromium binary required by the project Playwright version |
| 368 | active | P2 | open | low | dca83f79ebb8b80d018fd31d8674590e | sb wp eval parse errors are surfaced as a generic critical-site failure |
| 369 | active | P2 | open | low | 3f916f56a4f8e7079f7ee628c6e4324e | sb wp passthrough rejects an extra wp token with a misleading missing-file error |
| 370 | active | P2 | open | low | 5a59f5884d8cce77bae26f6cf010a194 | Post-processing resources JSON through Python quoting is fragile |
| 371 | active | P2 | open | low | 9f7c0552f338a5666ad5a71cc38fd08e | sb exec does not share the WordPress plugin filesystem used by sb wp |
| 372 | active | P2 | open | low | e093784cb284083b6768ee491beca80d | sb wp eval namespace escaping is easy to over-escape |
| 373 | active | P2 | open | low | ec4154df6becebdcaec9855c505c6fcf | sb wp eval quoting is easy to over-escape |
| 374 | active | P2 | open | low | 83017d4ae2ca0e5ad127abe1ea24c8ad | Sandbox WordPress image lacks pager required by wp help |
| 375 | active | P2 | open | low | b4afad744a9cd27f70125804a973eae0 | Sandbox logs command lacks bounded tail option |
| 376 | active | P2 | open | low | 91211eae7a0f4e0bc37340f3c8484fe2 | Review worktrees in system temp cannot be used by ensure |
| 377 | active | P2 | open | low | 70c5b9380bada51469c5cee70cdbf693 | Disposable PR review worktrees under macOS private temp are rejected |
| 378 | active | P2 | open | low | c5cc5e8182f990d7dde849eeae622d4f | Sandbox guide rejects isolated temp worktree outside configured roots |
| 379 | active | P2 | open | low | 63779c5a45a19b85bdda6c45f443dff5 | sb wp option get reports missing optional setting as command failure |
| 380 | active | P2 | open | low | 80ac2e2d134ca374c0fbd422adc651ea | Sandbox CLI is not discoverable from project repositories |
| 381 | active | P2 | open | low | d60dc6fa62fb623a41ce0afa5dd75133 | sb up lacks JSON output despite CLI-first automation |
| 382 | active | P2 | open | low | 2f5dd7379b043e0a99c76e1349c97241 | sandbox host logs line cap is undocumented in command context |
| 383 | active | P2 | open | low | e7a26e0b06f1b3cadf72947933edaefe | Hermes repo sync requires undisclosed confirmation flag |
| 384 | active | P2 | open | low | ea33c77d504c60a3a7cfb2ee546f7514 | Host logs environment selection is confusing and feedback flag names are undocumented |
| 385 | active | P2 | open | low | 6649d141452c845129f3ebf59df8e373 | Lenzora edge verification lacks curl in agent shell |
| 386 | active | P2 | open | low | 05936f990e374823e61c0ceb5f25e5c7 | skill show rejects documented project-dir placement |
| 387 | active | P2 | open | low | 4d0ef1c0135bcbd0379b9a5ceb2c895d | OpenRouter key profile is unavailable |
| 388 | active | P2 | open | low | b2eb916facc6ab2539553b5e2ed39823 | Feedback list limit range is absent from CLI skill |
| 389 | active | P2 | open | low | 60bcd100c479e42b4b9276d59ce8908e | feedback list rejects requested inventory limit |
| 390 | active | P2 | open | low | 6968a4b592fe4884e182e0f7dd9e5eda | WordPress command rejects documented local execution pattern |
| 391 | active | P2 | open | low | df47dc48756e87cc10b77fbea8bfe8df | CLI lacks  despite discoverability pattern |
| 392 | active | P2 | open | low | 57736970a6862ed1eb5eb59bab8fa530 | skill show rejects documented project-dir routing |
| 393 | active | P2 | open | low | 79acef9abd8dad40308d7db0456e28ee | Sandbox home output lacks a machine-readable cursor location |
| 394 | active | P2 | open | low | 097bcd12421d1c81054aca3028237b3f | Resource timeout regression missed subprocess import |
| 395 | active | P2 | open | low | a0022ceaf3a6594f4c621b2ab667dce1 | Feedback show rejects ledger ID prefixes |
| 396 | active | P2 | open | low | 008af940588e7c65225abbbf5251588b | Sandbox WordPress image lacks unzip for exact archive manifest verification |
| 397 | active | P2 | open | low | 8334692aacd13be9b091b339678a7cad | wp eval-file path is resolved only inside the WordPress container |
| 398 | active | P2 | open | low | d895fbb72d741555fe90b3577af32efd | WP eval-file wrapper cannot forward script options after documented separators |
| 399 | active | P2 | open | low | cf3fe2edc59788c8f9604a7cd71f3e3e | Sandbox npm wrapper does not document WP eval-file argument separators |
| 400 | active | P2 | open | low | 895e206fdb40629cacc88b6b6b648d89 | Sandbox WP option and eval probes provide noisy failure output for absent options and namespace quoting |
| 401 | active | P2 | open | low | d640f9c6f110d02c52b2bad359c2d67b | WP post-type field capability is not discoverable through Sandbox guide |
| 402 | active | P2 | open | low | e6a26df9b4d957a591bdc6d072e60bbe | login-url attempted on Basic Auth only environment |
| 403 | active | P2 | open | low | 7f0e27c15635d96a637b0d48e7a25381 | host login-url protection not visible in command help |
| 404 | active | P2 | open | low | 7df52abe67e97a43759b449ea6a11c8b | mktemp project suffix produces a slug rejected by sb init |
| 405 | active | P2 | open | low | 096fdaf53364cbcc86efd49ba25c4d98 | Temporary project under /tmp is refused after macOS canonicalizes it to /private/tmp |
| 406 | active | P2 | open | low | b2791536ba51a9a1ee0d7f2bb471294e | Disposable plugin fixture under macOS /tmp rejected |
| 407 | active | P2 | open | low | 35ed60860c8c1d2d034c8d5489d456d8 | feedback list rejects documented over-limit request |
| 408 | active | P3 | open | low | aa6452605024ed41759ec4f3e8a8050b | Retained remote gate exposed invalid benchmark fixture tree IDs |
| 409 | active | P3 | open | low | 65d5427835e56f60a2cd0637a40cc2e3 | T118 remote product gate failed on closed-list ordering mismatch |
| 410 | active | P3 | open | low | ff8b94dc66c07e836837b46a0030abc8 | Keyless WPDeveloper pro activation misses xspeed-pro (keyword list) |
| 411 | active | P3 | open | low | bcdb8d8f647df1652727abbcbf616ed4 | Plugin replacement can report success before detecting an unusable runtime archive |
| 412 | active | P3 | open | low | ade1ef130bd7178813711b00c29d3150 | Focused HSSB icon-default PHPUnit run found stale golden and payload-shape expectations |
| 413 | active | P3 | open | low | d79dcb78281523725565a2e3ad0eed6d | Full HSSB PHPUnit run found nine icon-default expectation mismatches |
| 414 | active | P3 | open | low | 5dd57aaddca483c747f87f3f0a33f24b | Focused HSSB PHPUnit filter reported four expectation failures |
| 415 | active | P3 | open | low | 3273804376c6c8d41b079a9fe6b3e15c | T118 lifecycle fixtures exposed cross-realm structuredClone incompatibility with Ajv array consts |
| 416 | closure | P0 | resolved | critical | 71be94307c0b2dfee1ef2640898bf1a0 | sb host apply exits 0 after a failed deploy, and its rollback can fail silently when the disk is full |
| 417 | closure | P0 | resolved | critical | 1f094d2f708e7c57cf128ac9635aa5af | Failed deploy leaves wrong remote instance registered |
| 418 | closure | P0 | resolved | critical | 0b420c9bf187cdce7a9e8be03d44dfda | target inference sent every project's sb ensure to the VPS instead of booting locally |
| 419 | closure | P0 | resolved | critical | 088652d49fdabbff77311353f251270b | Docker pool rollback restored configuration but left 20 previously running containers stopped |
| 420 | closure | P0 | resolved | critical | 3ef73e91af2e0fcc11c1c111591e9784 | Apply recreates ownership failure after explicit repair |
| 421 | closure | P0 | verified | critical | 19fe2251d017bb8bbbf0dd6b5d609d26 | Status JSON disclosed an autologin credential |
| 422 | closure | P0 | verified | critical | a1fc66d41f603894e18bc4092a0a9aba | Deploy ignores instance and provisions wrong project |
| 423 | closure | P0 | resolved | high | d176d75f2af85574e78aadbc5018fefa | Remote resource status timeout leaks traceback |
| 424 | closure | P0 | resolved | high | cd84b75dcfc5da657b6b5a95d35e54fb | Remote Docker pool transaction exceeded client timeout and failure envelope exposed encoded command detail |
| 425 | closure | P0 | resolved | high | 665bb8da6b1d86c8e1754e1c85e94dd0 | Fresh wordpress@1 child-image build fails at mbstring and init resolves the wrong project root |
| 426 | closure | P0 | resolved | high | 6481185910b63348f5c38308439049ca | A pre-subcommand --label is silently clobbered for six subcommands, targeting the wrong instance |
| 427 | closure | P0 | verified | high | e8ab77173bf1be69b65f7d1e65b42d49 | ensure JSON exposes credential-bearing login URL |
| 428 | closure | P0 | resolved | medium | 441022bfe8746ac26fa4b719fa0a7bb4 | Redacted sandbox_autologin URL is indistinguishable from a working one, and check_reachable's 10s budget false-negatives a ~190ms-RTT host |
| 429 | closure | P0 | not_applicable | low | 9fbb0f4b05ef8ee1c38fe7216aad1c48 | Full WP 7.1 suite exposed legacy API test state leakage |
| 430 | closure | P1 | resolved | high | c5a41bf90e8b801fe99b9f6abe7298e9 | Herd ensure acceptance unknown and registry surfaces disagree |
| 431 | closure | P1 | resolved | high | 861028b8a2315b6f9a82506b8cb47a94 | local resource scan cannot attribute container CPU or RAM |
| 432 | closure | P1 | resolved | high | 82a729ce37e1476c574327bc37c47a6e | Host-local deep collector capped directory walk at 120 seconds despite larger detached budget |
| 433 | closure | P1 | resolved | high | 7437b8a82a701af8126b9b2d3dbaf1ae | Hermes launcher accepted no --max-turns despite pinned release guidance |
| 434 | closure | P1 | resolved | high | 8f93049f0203d1b76e345b3447567079 | Preview label selector also ignored |
| 435 | closure | P1 | resolved | high | ef5fb478856eeb845b5b1ec177c260f8 | wp post list --search returned unrelated IDs and enabled unsafe cleanup |
| 436 | closure | P1 | resolved | high | 5fb893827632bc14bb55a7f051410723 | sandbox is adding steps for hosted apps: no deploy diagnose command, and secrets run cannot inject a credential pair |
| 437 | closure | P1 | resolved | high | c0037db18a5c0485b646df051a1cdd59 | Remote deploy of a composer-autoloaded WP plugin boots to a fatal: vendor/ is gitignored, so PSR-4 classes are missing |
| 438 | closure | P1 | resolved | high | c158edba811694913fe4cb4ccc3bbe2b | host apply recreated containers from a stale image; no build ran despite compose.build defaulting to true |
| 439 | closure | P1 | duplicate | high | 6987343f2dcacaedf7edc59ad81aa9d3 | Domain setup hits pre-existing XSpeed fatal during URL reconciliation |
| 440 | closure | P1 | resolved | high | 37d95e663fb02755608c87d5715d67f7 | host apply is silent for 30+ minutes and discards build output, so every deploy failure needs a manual forensic session |
| 441 | closure | P1 | resolved | high | a6223e1481c8800431839f6a4e891b80 | host apply should emit incremental progress; the job runtime already proves the pattern |
| 442 | closure | P1 | resolved | high | e7412a6dd087f348858f78348525f88e | Host apply cannot inject exact deployed source revision into runtime environment |
| 443 | closure | P1 | resolved | high | 17f8ea64f9128f062d3e6c9498945594 | sb test routes to a remote runner by default and fails with NetworkCapacityAdmissionError, even with a healthy local instance |
| 444 | closure | P1 | resolved | high | b69b39a5a25a10e244b7918e36e169ab | sb test silently runs REMOTE for a local project, and the working-tree push exceeds sb's own 120s timeout |
| 445 | closure | P1 | resolved | high | e25a8491a48f74accf72d1b96f6421e4 | Lenzora host apply returned generic remote command failure |
| 446 | closure | P1 | resolved | high | ee3a1f3864e28125881d0651d9a53c1a | ensure tried to replace a WordPress 7.1-RC3 instance with latest stable |
| 447 | closure | P1 | resolved | high | 9f0122e71b1ca2a956b20ab3442cf3fd | Scoped sb setup started unrelated project instances |
| 448 | closure | P1 | resolved | high | 6d5a269d610dcd57d4fe2df5a0223532 | sb init ignored explicit project-dir and instance target |
| 449 | closure | P1 | not_applicable | high | 00de46521aee1ff013aeabb5bd7de792 | Sandbox init rejects repository without compose or recognized project descriptor |
| 450 | closure | P1 | resolved | high | 6a6d2325ed47be0e1c73860e5432278c | Init with project-dir provisions wrong Docker target |
| 451 | closure | P1 | resolved | high | cc5a5f85457d80cacac085e2ec88a6af | Sandbox CLI crashes on duplicate --project-identity parser option |
| 452 | closure | P1 | resolved | high | b2fac0b406d02626aead84c36ccfb4ad | Herd lifecycle bypasses host-compatible mu-plugin provisioning |
| 453 | closure | P1 | resolved | high | c7be223a1e5f188aa88524fbbb3fe098 | Disposable exact-head WordPress ensure reports mount-state unavailable before first instance |
| 454 | closure | P1 | resolved | high | 7f014ae695c13eecc58607646a25d062 | In-place apply recreates containers then fails on unresolved declared plugin |
| 455 | closure | P1 | resolved | high | f37bd9aee69a896880eeb78c38c0d668 | Disposable exact-head WordPress apply rejects extension plan digest |
| 456 | closure | P1 | resolved | high | a6f1f5a8c4611f430d6963b08d4e791a | domains up reports clean ingress unavailable when another listener answers the hostname probe |
| 457 | closure | P1 | resolved | high | 74f5a1b8a8da1d6a90bc081bf3d2e3f4 | Activation supervisor restart fails while health state transitions to inactive |
| 458 | closure | P1 | resolved | high | f56063057f25aca4696e8abdbdd5b160 | Local idle-stop authority remains inactive when opted-in routes lack activation credentials |
| 459 | closure | P1 | resolved | high | 9275f8ff5da38293d452fe4d9e28a995 | Apply reported ready after WordPress critical error and lost pre-config instance routing |
| 460 | closure | P1 | resolved | high | 444b55cbd7ed702a8c143313d4b0dfb8 | Lifecycle apply unexpectedly upgrades WordPress core |
| 461 | closure | P1 | resolved | high | a05d10d59bf2e9bc1b49c58696bf752e | activation scan fails on invalid route metadata |
| 462 | closure | P1 | resolved | high | 1cfc73383f2132176c6dada6e6fde9e5 | Failed apply starts web containers without database service |
| 463 | closure | P1 | resolved | high | f0b3e8fdf1023c0585b5ce145716ead5 | WordPress init guessed unavailable Elementor Pro dependency for xSpeed |
| 464 | closure | P1 | resolved | high | 5e985bc296c1a22a281d8d0ae11fd8a5 | Remote deploy reached plugin activation but deployed plugin was absent |
| 465 | closure | P1 | resolved | high | 11a08aafc269d5e0a602313acfbec231 | All documented remote instance selectors are ignored |
| 466 | closure | P1 | resolved | high | 3a6cdc0284ae86c26c8bdbf27041457f | Deploy symlink defect reproduces on a clean instance, and the only workaround deadlocks future deploys on ownership |
| 467 | closure | P1 | resolved | high | 2413b7090bad3cc8f02ccf99b4247c0e | sb ensure/apply deadlock: apply uses --no-deps so db never starts |
| 468 | closure | P1 | resolved | high | f591d52ba4ba7d741824c1fce7397731 | sb ensure: a themes/plugins zip URL is misread as a local path, so the ready fast path refuses forever |
| 469 | closure | P1 | resolved | high | f3a4d100828e51e70f4ea6e862329e58 | Host logs fail when desired background service is absent |
| 470 | closure | P1 | resolved | high | 483a4fef967b99f8e1957030a35a00e1 | sb test always routes to the remote deploy path; --local is ignored and the run dies on an SSH git push timeout |
| 471 | closure | P1 | resolved | high | 8371d7f7589feaff75cd7d4ee17424ca | Explicit local instance selector was routed to configured remote |
| 472 | closure | P1 | resolved | high | b340f98a0df25f2c7817d7cac2bd652b | Registered remote reachability disagrees with brokered SSH |
| 473 | closure | P1 | resolved | high | 822262fe39df0c080ec49b562307d7bf | Remote workspace index remains incomplete |
| 474 | closure | P1 | resolved | high | 3a6e8c1a1fa71bde1a74b8b0eeb37e9c | Remote workspace index remains incomplete after provision |
| 475 | closure | P1 | resolved | high | 6e3ff886a6ccb6a37708abf705721189 | Sandbox cannot safely clean clearly temporary remote workspace stacks while the workspace index is incomplete |
| 476 | closure | P1 | resolved | high | 8bc9ca1c7fb7e293934bc6fcaa3e6506 | Remote workspace list rejects stale missing project directory |
| 477 | closure | P1 | resolved | high | 0139d6637973f4f1d7ceec87a2f45b75 | Remote ensure fails at post-install baseline snapshot: mariadb-dump cannot write /snapshots (Errcode 13) |
| 478 | closure | P1 | resolved | high | 5978c11e472eae799b5855fdab356e72 | Remote high-memory build was host-killed below its container cap |
| 479 | closure | P1 | resolved | high | 0384ab6b647c4b6f3ce1db9a348d496e | Remote job-start intermittently returns no acceptance payload or durable ledger entry |
| 480 | closure | P1 | resolved | high | fc79f41e549131be5d4d4edac49c4ad9 | workspace list/status/destroy all fail with workspace_index_incomplete on remote and host |
| 481 | closure | P1 | resolved | high | 02987b45f3796f5dee74d5328302a887 | Remote apply partially recreates runtime then fails on plugin permissions |
| 482 | closure | P1 | resolved | high | 6728d6f332f0cc7b76a4df0659236bc0 | sb host apply always passes --build and dies at the fixed 900s timeout for a large Next.js image |
| 483 | closure | P1 | resolved | high | 79d775b43cac5d1795b9a2fc52b0ff26 | Remote job-list control fails before returning retained records |
| 484 | closure | P1 | resolved | high | 3da039b402a4df0d831239c97a3cb987 | Detached remote job submission returned no job identity |
| 485 | closure | P1 | resolved | high | 343d1a5ae1c59b8f2754ee24ebf96b9d | Remote job-start can return silently without durable acceptance |
| 486 | closure | P1 | resolved | high | adde58a69cc69c26071e2c8555edbb0a | sb restore drops every table with no confirmation and no --yes flag |
| 487 | closure | P1 | resolved | high | 1f5484a5bd4548de4514719d62615605 | sb deploy symlinks the plugin to a deploy-src path that is not bind-mounted, so WordPress cannot see it |
| 488 | closure | P1 | resolved | high | 67ef77d6ad3d7acaaae60db41e0a5558 | VPS status ignores explicit instance selector |
| 489 | closure | P1 | resolved | high | 7c8f8b0b7896b8a72981a4d5b097a0dc | clean URL ingress can be intercepted by OrbStack wildcard ports |
| 490 | closure | P1 | resolved | high | 0e2d74b605ea48799f470d76d62058e4 | sb wp interleaves its own progress with command output, producing always-true test assertions |
| 491 | closure | P1 | resolved | high | ae744b9b19f4a1de4d85735bd83693a1 | Stale mkcert certificate forced broken HTTPS redirect for plain .tst route |
| 492 | closure | P1 | resolved | high | e11914b581d92c102eea700ba9456a2e | sandbox.config.<label>.json is root-only and silently no-ops when absent |
| 493 | closure | P1 | resolved | high | 8291ab9c74350d34a0d5ed4ff65e9262 | Nested host manifest applies Compose from wrong source root |
| 494 | closure | P1 | verified | high | 90e893e45556e215e5963d34738c3e89 | Global instance selector also ignored for remote status |
| 495 | closure | P1 | verified | high | 955b20457245aab5573cda8fd9951eec | Remote status ignores explicit instance selector |
| 496 | closure | P1 | verified | high | a48f3f90e30a211b6fbd438890d40f93 | Remote status ignores subcommand instance selector |
| 497 | closure | P1 | verified | high | 7acb4245dd8694fb4ee03b061db7e4ec | sb apply --project-dir rolls back a valid config change: reachability probe false-negative on an https instance |
| 498 | closure | P1 | verified | high | 2aa8e4725bfddb72c64b78b4b13c2391 | Sandbox launcher intermittently falls through into Python body |
| 499 | closure | P1 | verified | high | 54c1c9aed2fdf2bd620eee33eeb61b73 | Legacy write_secret() rewrites the whole personal secret file, destroying comments the broker preserves |
| 500 | closure | P1 | not_applicable | high | a562850cc9e3fed23450d74aa6ded0dd | Ensure silently rewrites a clean domain back to localhost when proxy setup falls back |
| 501 | closure | P1 | duplicate | high | e608c783333b62a9e4e6aad09e784ac1 | Activation enable fails while the existing supervisor is healthy |
| 502 | closure | P1 | resolved | medium | f212d4de78fea8a9dfdd783e88b58371 | sb ensure rejects valid temporary worktree paths with dot suffix |
| 503 | closure | P1 | resolved | medium | d354307ae60dafa7212b1e80599ea084 | 900s compose timeout is below the real build time (30-35 min observed), so a genuine rebuild cannot complete |
| 504 | closure | P1 | resolved | medium | 1d14d7610e00207008a295de97f3dff4 | Explicit remote instance status resolves against staged worktree instead of selected instance |
| 505 | closure | P1 | resolved | medium | 8129c05a7ed19355830dc7f956a2085c | No read-only hosted deployment status command |
| 506 | closure | P1 | resolved | medium | d43d5bc43376075199d7ba212843d190 | sb ensure created an uninstalled WordPress 7.0.2 runtime despite a local wpVersion 7.1 override |
| 507 | closure | P1 | not_applicable | medium | 7733a7ad9520cb118e7b5af6a4e4c3c0 | Ensure reports unavailable live source mount after runtime loss |
| 508 | closure | P1 | resolved | medium | 550d07ec93a0e063b398ff3d3febb77b | Clean-URL ingress down presents as a ~15min hang, and status does not name it |
| 509 | closure | P1 | resolved | medium | f3ac11b1bf16b60d679e7904c5872797 | job-status emits traceback for job in another registry |
| 510 | closure | P1 | resolved | medium | a85433d23181442576a5c121ac4db7b3 | Status ignores explicit instance outside a registered project |
| 511 | closure | P1 | resolved | medium | 3d51253741c65167ca40426ba41f6814 | apply cannot resolve instance created by a failed ensure run |
| 512 | closure | P1 | resolved | medium | 1e77bae54be958db001431c33cdf09e4 | sb apply reports an activation error while returning ready JSON |
| 513 | closure | P1 | resolved | medium | 42f1cb7877e00000775b4bfe80bf072d | Ensure waits silently before reporting unavailable Docker daemon |
| 514 | closure | P1 | resolved | medium | ec97adfcca24453c9382f7c6b0d78972 | Apply cannot reconcile an existing labelled WordPress instance |
| 515 | closure | P1 | not_applicable | medium | 9d3bfc17ec35d522c03ee1ad74677d1d | Sandbox status cannot resolve registered Lenzora instance from sandbox project directory |
| 516 | closure | P1 | resolved | medium | 7295d6e51c65ebc91c7ac7107929a8a8 | status project-dir fails after instances resolves project |
| 517 | closure | P1 | resolved | medium | f54749c5ec3326829b8cc038ed1aff58 | WordPress project status cannot suggest matching registered xSpeed instance |
| 518 | closure | P1 | resolved | medium | 73d32654f065c80cbd22abed00cd6197 | sb status --project-dir fails to resolve a registered project that apply --project-dir resolves fine |
| 519 | closure | P1 | resolved | medium | 45a42153c82d959f5bb6ee138c33f12c | Status lists a freshly ensured instance but cannot resolve it by exact project directory |
| 520 | closure | P1 | resolved | medium | c62320a3a2945c5b298e0a10e657e7d8 | Fresh WordPress ensure did not wire project plugin into instance |
| 521 | closure | P1 | not_applicable | medium | 8ad9e033916a505bc4d79ca1c5c5d6f9 | sb ensure aborts plugin provisioning on one unresolved declared plugin instead of continuing |
| 522 | closure | P1 | not_applicable | medium | 35b8491b9227efb3a0ca93cce100764c | ensure --create fails on disabled mcp-adapter declaration |
| 523 | closure | P1 | resolved | medium | e4617aeb19e9bb71ed421bd9bd1017ea | Sandbox remote workspace index is incomplete |
| 524 | closure | P1 | resolved | medium | 81f5852682fec8fcfa7a4c5edfec21eb | sb init scaffolds phpExtensions.profile that sb apply then refuses |
| 525 | closure | P1 | resolved | medium | 647f6478fc45fddad846fd1a29416928 | Host plan requires undocumented remote selection |
| 526 | closure | P1 | resolved | medium | b027d2ab742f2e38f493a6f7f6c4b1c7 | Sandbox jobs cannot use guide-resolved proof checkout |
| 527 | closure | P1 | resolved | medium | 3b9a21707d899904f31b331d5808fa20 | Host apply blocks release commit when unrelated concurrent files are dirty |
| 528 | closure | P1 | resolved | medium | b0d1a1e54a0e374f4fd8552777a8e428 | sb status declares --json but emits no JSON on any line |
| 529 | closure | P1 | verified | medium | bc9d782b7fb49ec930ef3f544f9977ff | Remote service status omits installed runtime revision |
| 530 | closure | P1 | verified | medium | d2dc1ac3e5ab810fc6ff953ac8d41e19 | sb test remote wrapper forwards outer CLI flags to PHPUnit |
| 531 | closure | P1 | verified | medium | 14d2c5e5874dc5421522e100dbb7e4aa | Stopped WordPress instance cannot attest mounts during ensure |
| 532 | closure | P1 | verified | medium | 3c184f3c2534479d4b7ab32018af8b4d | Secret broker child exit status is not surfaced as a command failure |
| 533 | closure | P1 | verified | medium | fda8e3c507f585610e7054766f3ba99a | sb ensure reports the instance ready after a sandbox.config.json mount change, but keeps the stale bind mount |
| 534 | closure | P1 | verified | medium | 99d801d394b6866eb6d337b5fc8c3fba | ensure blocks on a stopped local container set with a misleading mount-state remedy |
| 535 | closure | P1 | verified | medium | 7102d6aeb037160105353718b8658db6 | sb apply starts only wp+nginx (--no-deps), so a missing db surfaces as a bogus plugin error |
| 536 | closure | P1 | verified | medium | ca3e8efe4ad21508bfe590088ea4fc03 | Ensure does not reconcile newly added plugin mount |
| 537 | closure | P2 | resolved | medium | f13ce98a2b1cf9208cb70e9d32e0b127 | Scoped sb wp inspection hung after core reported installed |
| 538 | closure | P2 | duplicate | medium | 94b2e9ea0203b9552b824a0edef3c6c6 | Guide-documented sb wp separator is forwarded literally |
| 539 | closure | P2 | resolved | medium | e717790ccfb266def874bd5dab31b47e | Plugin Check targets project slug instead of installed plugin map key |
| 540 | closure | P2 | duplicate | medium | 4a9d18478e7296c2ffeeaaeb6d8f0101 | WordPress guide emits unusable wp separator syntax |
| 541 | closure | P2 | duplicate | medium | 3f858af223a901e8cf2389eca038d180 | Generated guide wp separator is passed as WP command |
| 542 | closure | P2 | resolved | medium | e90a91ce4ba43da1f614711f17bd3a9c | WordPress guide advertises unusable separator syntax |
| 543 | closure | P2 | resolved | medium | 91a5804528ffcc223a97978c857291f0 | A WP instance cannot resolve its own published localhost:<port>, so any plugin loopback to home_url() fails |
| 544 | closure | P2 | resolved | medium | 1a16d09ab8232fcfd19eb23eb5700b1d | Project directory did not resolve its known labeled local instance |
| 545 | closure | P2 | resolved | medium | 6374e2268d9d8d1ce93db1bbe73e2a6a | sb init on a WP plugin repo scaffolds no mapping for the repo's own plugin |
| 546 | closure | P2 | not_applicable | medium | 715ecbd7b9d2509b3d45c226503120dc | Local init rejects optional disabled plugins |
| 547 | closure | P2 | resolved | medium | 7586c2da39e8c6248488d9333ee5f3cb | visit URL refuses a known instance when invoked outside its registered project directory |
| 548 | closure | P2 | resolved | medium | 618665f43d1c4373a78073ec365ae91e | visit rejects generic Compose project before browser route verification |
| 549 | closure | P2 | resolved | medium | c335f32ed75eedc8446d074af5517a98 | secrets run injects only one key, blocking paired credentials (S3/R2 access key id + secret) |
| 550 | closure | P2 | resolved | medium | 22d52bec13bcfae8bbcd8a84518baff9 | WordPress instance cannot loopback-request its own .tst host |
| 551 | closure | P2 | resolved | medium | 00b1e17ef9e48163053e874f8cd3dbc5 | No secret-safe authenticated health verification for Basic Auth host |
| 552 | closure | P2 | resolved | medium | cf5e49ed74423175e35d7c224dac473f | Known generic instance cannot resolve from its project directory |
| 553 | closure | P2 | resolved | medium | ef4ec661e33eeabb4c96b1d53d21b06d | visit rebuilt a valid CLI venv and then failed on missing pip shim |
| 554 | closure | P2 | resolved | medium | b690505252b3b715c48b6b10a4dbade4 | sb guide, the designated agent entry point, lists about 9 percent of the command surface |
| 555 | closure | P2 | resolved | medium | ad190c71bb8c1f6148e348e0c01ce797 | The feedback log has no read path and no egress: details are write-only |
| 556 | closure | P2 | resolved | medium | 81f43e6f645366f2593be67d9b05cdd4 | Redaction is keyword-only, so common bare provider tokens are stored verbatim |
| 557 | closure | P2 | verified | medium | 9a383ec302d7b53bb45262336576ae1b | Sandbox CLI discoverability issue with instance-listing command |
| 558 | closure | P2 | verified | medium | bd9b0f950c0dbe05e835a3a264fcc9e1 | Sandbox CLI discoverability issue with instance-listing command |
| 559 | closure | P2 | duplicate | medium | 910bc8c9e0cd0009d15b6698e2f3b35f | secrets run does not propagate trusted child failure |
| 560 | closure | P2 | resolved | medium | 13bfb0ed2825f9cf1ef8dae179f5db66 | Domains documentation points to an unavailable doctor action |
| 561 | closure | P2 | not_applicable | medium | 695cd378486da97b45ab284f44df040d | Doctor command rejects a valid instance when run from Sandbox root |
| 562 | closure | P2 | duplicate | low | f846861e26a13d521ae3ff1f330ae6c0 | sandbox host status action is unavailable |
| 563 | closure | P2 | duplicate | low | dca9e6e6abf8e9abf67fc886d105232e | sb wp rejects project-dir routing for manual fixture reset |
| 564 | closure | P2 | resolved | low | 12f3901f0d77fd0d9da1626145e79bec | sb wp rejects project-dir used by project-scoped automation |
| 565 | closure | P2 | resolved | low | 9be86009dd1cfb404a39176e43184fae | WordPress subcommand rejects explicit local selector |
| 566 | closure | P2 | resolved | low | 369b23c71360d455a664589c18a20b6b | Activation status and scan reject the documented-looking --json placement |
| 567 | closure | P2 | resolved | low | 7400679a8bd181379d48834e88f01802 | activation status rejects harmless json selector |
| 568 | closure | P2 | resolved | low | 6bc4c6d5e0a70e0a048030d694ad76f9 | Compact active-job parser failed on null data |
| 569 | closure | P2 | resolved | low | 15d1625b2d1f52c15b595b39bc3120d9 | Project guide advertises unavailable ./sb wrapper |
| 570 | closure | P2 | resolved | low | 2b080bf5b7fec8dc1199aa4bba812a78 | CLI and MCP disagree on project identity: same report, different record |
| 571 | closure | P2 | resolved | low | f90c67127f746e37e7a0d58a40acd4e8 | feedback list reports a partial invalid_record_count as if it were complete |
| 572 | closure | P2 | verified | low | 439e970b010909485adb7362aa4b23e1 | Remote revision status parser reports unknown for a proven unit |
| 573 | closure | P2 | verified | low | 93bdc880370f90ad3e0dabe52ad9cd27 | sb ensure stops on mount drift but the guide omits sb apply as the required recovery |
| 574 | closure | P2 | verified | low | 757a756d5592ce5319f3e155914f5369 | No supported instance list action |
| 575 | closure | P2 | verified | low | 35217943eb0a5c9b0938738000d6d371 | Instance listing command is not discoverable |
| 576 | closure | P2 | verified | low | e1d98c9872a1edd5482f8062cd5ff354 | Instance subcommand lacks read-only list action |
| 577 | closure | P2 | verified | low | c892e6d411297f910f49cdc878fdc033 | Sandbox CLI lacks a standard version flag |
| 578 | closure | P2 | verified | low | 54b3f69511df4eb5dcc81e71e9f77560 | Standard version flag is not supported |
| 579 | closure | P2 | not_applicable | low | 108318d9f208a78beffc95798261e92c | WordPress plugin state drift observed between sessions |
| 580 | closure | P2 | resolved | low | 525d979da7fc4f2652eab8475c0161f9 | activation status rejects JSON output flag after the subcommand |
| 581 | closure | P2 | resolved | low | 12f503ddf72d709f4b22a0fee1cd435f | visit rejects JSON output flag after the URL |
| 582 | closure | P3 | not_applicable | medium | 2eb242d04b4b9142fb5a94fb4b9587c8 | combined CLI test run can hang in sb instances subprocess |
| 583 | closure | P3 | verified | low | 3b6356fd0b3346d9bdf8e03818a3308f | Provide a non-mutating Sandbox CLI version command |
| 584 | closure | P3 | not_applicable | low | 70efc11f0057be8a815c8c81edc9dd48 | feedback export rejects max-bytes above its documented upper bound |
| 585 | closure | P3 | not_applicable | low | 52e75cf8bdc07aaebf8adc9ea2cada5f | feedback review accepts oversized evidence argument before rejecting it |
| 586 | closure | P3 | not_applicable | low | ede0e5e74cebecb1633a9041fd3d60a7 | Doctor does not run from the Sandbox repository root without a registered project |
| 587 | closure | P3 | duplicate | low | 0c09b4d67b155094e957333f4b3b6927 | feedback review rejects evidence longer than its bounded limit |
| 588 | closure | P3 | not_applicable | low | e35b5a7eb09066b2932b19fd908b81a9 | status from Sandbox checkout has no local instance context |
| 589 | closure | P3 | not_applicable | low | 1dc1e1a6851fbddd186c29450fe06bc2 | feedback JSON piping can surface BrokenPipeError when downstream parser exits |
| 590 | closure | P3 | not_applicable | low | ce1f7d87ad17d203062620621a8127bf | feedback list returns records that show cannot resolve by ID |
