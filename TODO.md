# Sandbox TODO

Updated: 2026-08-19. Structure: standing engineering work (§1–§3) runs continuously; product delivery is phased, each phase owned by a standalone PRD under `todo/` (index and decision list in `todo/README.md`). Finished work is deleted, not archived — history lives in git and the spec ledgers.

Sources: `[guardian]` = 2026-08-12 product feedback + current Sandbox evidence · `[mail]` = 2026-08-06 live host probes + provider research · `[dns]` = 2026-08-06 Cloudflare zone work · `[ops]` = observed on the remote host · `[herd-parity]` = 2026-08-11 local probes + official Herd/Laravel/Docker/Node/pnpm/MySQL research under `.ai/research/2026-08-11-herd-sandbox-parity/` · `[vercel]` = 2026-08-19 official Vercel Sandbox documentation review under `.ai/research/2026-08-19-vercel-sandbox/` · `[prd NN]` = detailed brief in `todo/NN-*/prd.md`.

Direct-delivery research is done (2026-08-06: port 25 open, IP unlisted, host is Contabo not Scaleway). Do not re-run the provider comparison; the open questions that remain are listed in `todo/01-outbound-mail/prd.md` §11.

## 0. Immediate operational blockers (P0; before new remote harness work)

- [ ] Add network-capacity admission evidence before remote workspace/test provisioning:
  report usable subnet capacity (or a bounded unavailable state), fail before staging
  when a network cannot be allocated, identify the owning resource class, and point to a
  reviewed cleanup/capacity plan. Cover pool exhaustion with a deterministic regression;
  do not infer capacity from disk space or network count alone. `[ops → prd 00 §5 P0]`
- [ ] Complete the durable workspace index on `scaleway-sandbox` without guessing
  ownership. The current metadata-only plan exposes 32 unresolved legacy records and
  therefore correctly refuses a complete list. Preserve every legacy record, require
  exact job/project evidence or operator mapping, and keep migration separate from
  reset, destroy, network release, and cleanup authority. `[ops · observability]`

## 1. Loose ends (hours; found while shipping, none blocking)

- [ ] `hermes.asb.bd` serves the Cloudflare Access 302 for `/robots.txt` — Access runs ahead of Workers, so the deny never reaches it. Accepted: the host is Access-gated, nothing is crawlable. Revisit only if the route ever goes public. `[dns]`
- [ ] Worker route patterns don't match a query string, so `/robots.txt?x=1` falls through to the origin (523 on hostnames with no vhost). Cosmetic — crawlers request it bare. `[dns]`
- [ ] `replay.lenzora.dev` returns 525 (TLS handshake failed at origin) — unrelated to mail, spotted during the robots sweep. `[ops]`

## 2. Platform integrity (continuous)

- [ ] `sandbox/core/_cloudflare.py` upserts address records only — no TXT/CNAME. Blocks every DNS-automating feature, mail first. `[dns → prd 01 §4.4]`
- [ ] Instance mail is a dead end by construction: `_write_mail_muplugin` rewrites From to an invalid no-TLD address so `wp_mail()` fails loudly. Right for a laptop, wrong for a public preview. `[mail → prd 01]`
- [ ] One IP, one reputation, shared by every preview and every permanent site. Needs per-instance rate limiting before sending is switched on widely. `[mail → prd 01 §10]`
- [ ] Research Caddy's official [PHP serving patterns](https://caddyserver.com/docs/caddyfile/patterns#php): compare `php_fastcgi` with PHP-FPM and FrankenPHP's `php_server` against the current Caddy-ingress plus nginx/Apache/PHP-FPM design. Evaluate per-instance isolation, PHP-version and socket ownership, WordPress/static-file routing, operability, rollback, and whether any runtime change is warranted before proposing one.
- [ ] Review the T3 Code app repository and identify any relevant patterns or lessons that could be applied to Sandbox. Research only; propose changes separately before implementation.
- [ ] Make remote deployment and workspace staging ignore macOS AppleDouble sidecars (`._*`) without ignoring ordinary dotfiles. Cover uncommitted-diff archive upload and Sandbox-runtime source upload; acceptance is a remote staging regression proving no sidecar reaches deploy targets or workspaces while valid dotfiles and intended files remain byte-identical, with skipped-sidecar counts in safe diagnostics. `[ops · remote staging]`

## 3. Environment lifecycle primitives `[vercel]`

Four primitives from the 2026-08-19 Vercel Sandbox review that Sandbox lacks and
that map onto queued work. Full findings, local gaps, and what is deliberately
not adopted: `.ai/research/2026-08-19-vercel-sandbox/findings.md`. Research only
— each item needs its own design pass before implementation.

- [ ] Split instance identity from the running session, and make storage-pressure
  relief hibernate rather than reap: stop the session, snapshot the filesystem,
  resume transparently on the next `sb wp` / `visit` / MCP call. Today `./sb`
  offers `down` (stops, reclaims nothing durable) or `clean` (destroys data) with
  no state in between, which is why pressure relief currently costs the user
  their site. Feeds spec 043. `[vercel → findings §1, §3.1]`
- [ ] Add `sb fork <instance>` — seed a new instance from another's current
  snapshot plus config. Targets the most expensive repeated operation we have:
  every `run_e2e` worker and every `ci_run` matrix cell installs WordPress from
  scratch. Copy the two documented sharp edges: secrets are not carried into the
  fork, and tag/port overrides replace rather than merge. Needs its own spec.
  `[vercel → findings §3.2]`
- [ ] Make snapshot retention declared policy instead of manual hygiene:
  expiration TTL, keep-last-N, and an explicit rule for what happens to evicted
  snapshots, enforced at capture time. `./sb snapshot` currently takes only
  `--force`/`--db-only`, so snapshots grow unbounded inside the class spec 042's
  tiered cleanup refuses to touch. Feeds specs 042/043. `[vercel → findings §3.3]`
- [ ] Carry the explicit per-mount access mode (`read-write` / `read-only`) into
  spec 044's shared node store and git dedup design. Vercel Drives — one named
  volume, many consumers, mode declared at the mount site — is independent
  validation of that shape and of what makes a shared store safe to attach
  widely. `[vercel → findings §3.4]`
- [ ] Meter per instance — active CPU excluding I/O wait, memory-hours, egress
  bytes, snapshot bytes, last-touched — so a pressure scheduler can rank victims.
  `sb resources status` attributes disk only, which is not enough to choose what
  to hibernate. Feeds spec 043. `[vercel → findings §3.5]`
- [ ] Treat an attached consumer as a lease: never reap or hibernate an instance
  with a live shell, an open browser session, or a running job. `[vercel → findings §3.6]`
- [ ] Probe whether an instance's `wp_remote_get()` to its own HTTPS domain
  passes TLS verification. `NODE_EXTRA_CA_CERTS` is set on the host only
  (`sandbox/commands/config_setup.py`); no CA-bundle variables reach instance
  containers and no `update-ca-certificates` step exists in the Compose sources.
  State the observed result before proposing a fix. `[vercel → findings §2]`
- [ ] Add free-form `key=value` tags to instances, filterable in `sb instances`,
  so retention and cleanup policy can target a set instead of matching derived
  names. Today only a derived name and an operator-chosen `label` exist.
  `[vercel → findings §3.8]`
- [ ] Deferred unless the threat model changes: per-instance egress policy
  (deny-all base, domain/CIDR allowlists). Only relevant if Sandbox ever runs
  untrusted plugin or agent-generated code; not needed for today's trusted-code
  workload. Related: Sandbox isolates with Docker on a shared kernel, shared
  across workspaces on the remote host, so documentation must keep saying
  trusted code only. `[vercel → findings §3.9, §4]`

## Phase 0 — WordPress Plugin Release Guardian / Operations Agent `[prd 00]`

Make Sandbox's next 12 months one coherent WordPress release-safety product.
Deterministic checks own the verdict; AI operates bounded tools, triages, and
explains. Read-only is the default, mutations need revision-bound approval, and
real adoption plus published negative evidence are release criteria.

### P0 — Trustworthy release decision (months 1–4)

- [ ] Treat reliable remote harness admission as the first Guardian prerequisite: close
  TODO §0 network-capacity and structured-observability blockers before claiming the
  compatibility/security matrix is runnable or collecting pilot latency/reliability
  evidence. Infrastructure failure remains a fail-closed verdict, never a skipped pass.
  `[ops → prd 00 §5 P0]`
- [ ] Resolve the five product decisions in `todo/00-wordpress-plugin-release-guardian/prd.md` §11 before formal specification: security scanner/ruleset, default required compatibility matrix, baseline-mutation authority, evidence retention/privacy/cost budget, and first design partner/release policy. Run `speckit-refine` and require the independent Sol High readiness review. `[guardian → prd 00 §11]`
- [ ] Define a separately declared Guardian MCP/Abilities profile: read-only discovery by default; explicit schemas, scopes, side-effect/data classes, timeouts, and audit behavior; undeclared abilities absent. Arbitrary PHP/shell/WP-CLI/SQL/file writes and release/deploy/publish operations are not safe Guardian abilities. `[prd 00 §4.2]`
- [ ] Add revision-bound, expiring, replay-safe approval gates for any permitted mutation and prove wrong-revision, stale, over-scoped, or unauthorized requests stop before side effects. Keep autonomous publishing, releasing, deployment, merge, tag, and production mutation out of scope. `[prd 00 §4.2, §8]`
- [ ] Ship one immutable-revision Guardian run that composes Plugin Check, declared PHPUnit suites, isolated required WordPress/PHP matrix cells, and a pinned deterministic security scan through bounded durable jobs. Observe every detached job to terminal state. `[prd 00 §5 P0, §6]`
- [ ] Make the deterministic verdict fail closed: any required `fail`, `partial`, `unavailable`, `timed_out`, `cancelled`, infrastructure error, stale evidence, or unevaluated cell blocks `ready`; an AI explanation or prior baseline cannot override it. `[prd 00 §2, §4.1]`
- [ ] Record an evidence envelope from the first runnable slice: revision/policy/tool versions, normalized outcomes, artifact digests, tool traces, approvals/mutations, retry lineage, terminal lifecycle, per-step/end-to-end latency, resource use, and AI tokens/cost, with secret-safe redaction. `[prd 00 §4.3, §5 P0]`
- [ ] Recruit pilot candidates during P0: identify at least five plausible outside users and one plugin-team candidate, then secure one design partner before expanding the gate catalog. Pilot discovery is not yet counted as adoption. `[prd 00 §5 P0, §10]`

### P1 — Explainable operations and measured quality (months 4–8)

- [ ] Add evidence-grounded AI triage that separates product failures from infrastructure failures, groups findings, states uncertainty, and cites immutable gate/cell/artifact/trace IDs. Unsupported claims are labeled and scored as failures; AI never changes the verdict. `[prd 00 §5 P1]`
- [ ] Where the pilot needs bounded changes, produce a revision-bound mutation plan and require fresh human approval before creating a candidate patch or proposing a selected baseline update; rerun deterministic verification afterward. `[prd 00 §5 P1]`
- [ ] Build a versioned evaluation set from real, reproducible plugin defects across compatibility, Plugin Check, PHPUnit, security, flaky/infrastructure, and insufficient-evidence cases. Keep reviewed expected outcomes separate from agent prompts and production release data. `[prd 00 §5 P1, §8]`
- [ ] Publish an internal quality/operations scorecard: false-ready count, gate accuracy, triage usefulness and citation support, policy violations, completion/retry rates, p50/p95 per-gate and end-to-end latency, compute/tool use, AI tokens/cost, and human review time. Show distributions and failures, not only averages. `[prd 00 §5 P1]`

### P2 — Adoption and public evidence (months 8–12)

- [ ] Make onboarding usable by an outside maintainer without Sandbox-maintainer intervention on the happy path: configure policy, understand permissions, run Guardian, inspect evidence, and report a problem. `[prd 00 §5 P2]`
- [ ] Achieve either five outside users each completing a real plugin release evaluation or one real plugin team integrating Guardian into its release workflow. Demos, interviews, cloned repos, and maintainer-operated runs do not count. `[prd 00 §5 P2, §8]`
- [ ] Publish measured results covering evaluation design, supported scope, sample size, correctness, triage quality, latency/cost distributions, limitations, and reproducible methodology. `[prd 00 §5 P2]`
- [ ] Publish one real failure/postmortem case—missed defect, near miss, unsafe AI suggestion, flaky gate, or operational incident—with impact, timeline, detection, causes, corrective actions, and changed guarantees. `[prd 00 §5 P2]`

## Phase 1 — Outbound mail `[prd 01]`

Direct delivery from the Sandbox host, no paid provider. The research is settled; what follows is build.

- [ ] **Manual, blocking:** set rDNS for `212.47.72.49` and `2a02:c207:2343:3::1` to `mail.asb.bd` in the Contabo panel. Nothing else in this phase can be proven until this lands. `[prd 01 §4.2]`
- [ ] `mail.asb.bd` A + AAAA in Cloudflare, **grey-cloud** — proxied records break forward-confirmed rDNS. `[prd 01 §4.2]`
- [ ] TXT/CNAME upsert in the Cloudflare client, with the same backup-before-mutate discipline as the 2026-08-06 Email Routing cutover. `[prd 01 §4.4]`
- [ ] Postfix + OpenDKIM provisioner role: send-only, Docker-network bound, `mynetworks` restricted, `smtp_address_preference = ipv4`, no public :25 listener. `[prd 01 §4.1]`
- [ ] `sb mail setup <domain>` — generate DKIM keypair, write DKIM TXT + merged SPF (Email Routing's include must survive) + DMARC. `[prd 01 §4.4]`
- [ ] `sb mail status <domain>` — report all eight preconditions with observed values, so a delivery failure names its own cause. Ships **before** `mode: send` is documented. `[prd 01 §5]`
- [ ] Relax `asb.bd` DMARC to `adkim=r; aspf=r` and add `rua=`; strict alignment makes the zone reject its own subdomain senders. Owner decision 4 first: command-driven or human-only. `[prd 01 §4.2]`
- [ ] mu-plugin modes `capture` (default) / `send` (allowlisted) / `send`, From derived from the instance domain, non-allowlisted recipients captured **and logged**, never dropped. `[prd 01 §4.3]`
- [ ] `mail:` block in `sandbox.config.json` and `sandbox.hosting.yml`, beside the `robots:` key. `[prd 01 §4.5]`
- [ ] Delivery proof: preview password reset reaching a Gmail **inbox** with DKIM pass + SPF pass + DMARC aligned, headers recorded in the spec ledger. `[prd 01 §9]`
- [ ] Resolve the five owner decisions in `todo/01-outbound-mail/prd.md` §11 before `speckit-specify`. `[prd 01 §11]`

## Phase 2 — Herd-equivalent polyglot development stacks `[prd 02]`

Reproduce the observable Laravel + database + Node development contract through
Sandbox without claiming that Linux containers are Laravel Herd. Compose remains
the supported default; the detected Herd adapter stays non-adoptable until its own
proof gates pass.

- [ ] Resolve the five owner decisions in `todo/02-herd-equivalent-polyglot-stacks/prd.md` §12 before `speckit-specify`: equivalence target, frontend execution strategy, MySQL 8.0.27 emulation versus a native-ARM version, application-environment delivery scope, and related-project ownership. `[herd-parity → prd 02 §12]`
- [ ] Add a read-only parity preflight that distinguishes compatible, mismatched, unavailable, and unverified PHP/extension, Node/package-manager, database, port, environment-source, routing, and health facts. It must never print secret values or call a mismatch “exact.” `[prd 02 §5.1, §5.5]`
- [ ] Make explicit Laravel and Node initialization useful without a pre-existing Compose file: produce reviewable proposals from inert manifests, report every inferred value and uncertainty, and execute no repository command before a separate start action. `[prd 02 §5.2]`
- [ ] Support a project-owned relation between backend and frontend instances with stable service discovery, ordered health, bounded diagnostics, and cleanup that cannot delete a sibling project or its persistent database. `[prd 02 §5.3]`
- [ ] Define a secret-safe application environment handoff for registered `.env*` sources. Preserve the broker's no-raw-read policy, grant only declared consumers/keys, keep values out of argv/logs/registry/committed config, and do not mistake Compose interpolation for container environment delivery. `[prd 02 §5.4]`
- [ ] Make requested-port conflicts actionable: identify only safe process metadata, refuse implicit takeover, and require a fresh explicit stop/replace decision before claiming ports such as 3000 or 8000. `[prd 02 §5.6]`
- [ ] Prove the selected product contract on representative Laravel 12 + MySQL and Next.js + pnpm projects on Apple Silicon, including edit/reload behavior, backend calls from browser and server components, database persistence, repeated lifecycle operations, test commands, secret non-disclosure, and a measured comparison with the prior host-native baseline. `[prd 02 §8]`
- [ ] Keep generic incumbent-Herd execution outside this phase unless the owner explicitly selects native equivalence. If selected later, it needs a separate PRD and must remain labeled trusted shared-host/lower-isolation; it may not weaken Compose defaults or reuse the WordPress-only unproven adoption claim. `[prd 02 §6, §12]`
