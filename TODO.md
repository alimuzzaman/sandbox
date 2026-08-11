# Sandbox TODO

Updated: 2026-08-11. Structure: standing engineering work (§1–§2) runs continuously; product delivery is phased, each phase owned by a standalone PRD under `todo/` (index and decision list in `todo/README.md`). Finished work is deleted, not archived — history lives in git and the spec ledgers.

Sources: `[mail]` = 2026-08-06 live host probes + provider research · `[dns]` = 2026-08-06 Cloudflare zone work · `[ops]` = observed on the remote host · `[herd-parity]` = 2026-08-11 local probes + official Herd/Laravel/Docker/Node/pnpm/MySQL research under `.ai/research/2026-08-11-herd-sandbox-parity/` · `[prd NN]` = detailed brief in `todo/NN-*/prd.md`.

Direct-delivery research is done (2026-08-06: port 25 open, IP unlisted, host is Contabo not Scaleway). Do not re-run the provider comparison; the open questions that remain are listed in `todo/00-outbound-mail/prd.md` §11.

## 1. Loose ends (hours; found while shipping, none blocking)

- [ ] `hermes.asb.bd` serves the Cloudflare Access 302 for `/robots.txt` — Access runs ahead of Workers, so the deny never reaches it. Accepted: the host is Access-gated, nothing is crawlable. Revisit only if the route ever goes public. `[dns]`
- [ ] Worker route patterns don't match a query string, so `/robots.txt?x=1` falls through to the origin (523 on hostnames with no vhost). Cosmetic — crawlers request it bare. `[dns]`
- [ ] `replay.lenzora.dev` returns 525 (TLS handshake failed at origin) — unrelated to mail, spotted during the robots sweep. `[ops]`

## 2. Platform integrity (continuous)

- [ ] `sandbox/core/_cloudflare.py` upserts address records only — no TXT/CNAME. Blocks every DNS-automating feature, mail first. `[dns → prd 00 §4.4]`
- [ ] Instance mail is a dead end by construction: `_write_mail_muplugin` rewrites From to an invalid no-TLD address so `wp_mail()` fails loudly. Right for a laptop, wrong for a public preview. `[mail → prd 00]`
- [ ] One IP, one reputation, shared by every preview and every permanent site. Needs per-instance rate limiting before sending is switched on widely. `[mail → prd 00 §10]`
- [ ] Research Caddy's official [PHP serving patterns](https://caddyserver.com/docs/caddyfile/patterns#php): compare `php_fastcgi` with PHP-FPM and FrankenPHP's `php_server` against the current Caddy-ingress plus nginx/Apache/PHP-FPM design. Evaluate per-instance isolation, PHP-version and socket ownership, WordPress/static-file routing, operability, rollback, and whether any runtime change is warranted before proposing one.
- [ ] Make remote deployment and workspace staging ignore macOS AppleDouble sidecars (`._*`) without ignoring ordinary dotfiles. Cover uncommitted-diff archive upload and Sandbox-runtime source upload; acceptance is a remote staging regression proving no sidecar reaches deploy targets or workspaces while valid dotfiles and intended files remain byte-identical, with skipped-sidecar counts in safe diagnostics. `[ops · remote staging]`

## Phase 0 — Outbound mail `[prd 00]`

Direct delivery from the Sandbox host, no paid provider. The research is settled; what follows is build.

- [ ] **Manual, blocking:** set rDNS for `212.47.72.49` and `2a02:c207:2343:3::1` to `mail.asb.bd` in the Contabo panel. Nothing else in this phase can be proven until this lands. `[prd 00 §4.2]`
- [ ] `mail.asb.bd` A + AAAA in Cloudflare, **grey-cloud** — proxied records break forward-confirmed rDNS. `[prd 00 §4.2]`
- [ ] TXT/CNAME upsert in the Cloudflare client, with the same backup-before-mutate discipline as the 2026-08-06 Email Routing cutover. `[prd 00 §4.4]`
- [ ] Postfix + OpenDKIM provisioner role: send-only, Docker-network bound, `mynetworks` restricted, `smtp_address_preference = ipv4`, no public :25 listener. `[prd 00 §4.1]`
- [ ] `sb mail setup <domain>` — generate DKIM keypair, write DKIM TXT + merged SPF (Email Routing's include must survive) + DMARC. `[prd 00 §4.4]`
- [ ] `sb mail status <domain>` — report all eight preconditions with observed values, so a delivery failure names its own cause. Ships **before** `mode: send` is documented. `[prd 00 §5]`
- [ ] Relax `asb.bd` DMARC to `adkim=r; aspf=r` and add `rua=`; strict alignment makes the zone reject its own subdomain senders. Owner decision 4 first: command-driven or human-only. `[prd 00 §4.2]`
- [ ] mu-plugin modes `capture` (default) / `send` (allowlisted) / `send`, From derived from the instance domain, non-allowlisted recipients captured **and logged**, never dropped. `[prd 00 §4.3]`
- [ ] `mail:` block in `sandbox.config.json` and `sandbox.hosting.yml`, beside the `robots:` key. `[prd 00 §4.5]`
- [ ] Delivery proof: preview password reset reaching a Gmail **inbox** with DKIM pass + SPF pass + DMARC aligned, headers recorded in the spec ledger. `[prd 00 §9]`
- [ ] Resolve the five owner decisions in `todo/00-outbound-mail/prd.md` §11 before `speckit-specify`. `[prd 00 §11]`

## Phase 1 — Herd-equivalent polyglot development stacks `[prd 01]`

Reproduce the observable Laravel + database + Node development contract through
Sandbox without claiming that Linux containers are Laravel Herd. Compose remains
the supported default; the detected Herd adapter stays non-adoptable until its own
proof gates pass.

- [ ] Resolve the five owner decisions in `todo/01-herd-equivalent-polyglot-stacks/prd.md` §12 before `speckit-specify`: equivalence target, frontend execution strategy, MySQL 8.0.27 emulation versus a native-ARM version, application-environment delivery scope, and related-project ownership. `[herd-parity → prd 01 §12]`
- [ ] Add a read-only parity preflight that distinguishes compatible, mismatched, unavailable, and unverified PHP/extension, Node/package-manager, database, port, environment-source, routing, and health facts. It must never print secret values or call a mismatch “exact.” `[prd 01 §5.1, §5.5]`
- [ ] Make explicit Laravel and Node initialization useful without a pre-existing Compose file: produce reviewable proposals from inert manifests, report every inferred value and uncertainty, and execute no repository command before a separate start action. `[prd 01 §5.2]`
- [ ] Support a project-owned relation between backend and frontend instances with stable service discovery, ordered health, bounded diagnostics, and cleanup that cannot delete a sibling project or its persistent database. `[prd 01 §5.3]`
- [ ] Define a secret-safe application environment handoff for registered `.env*` sources. Preserve the broker's no-raw-read policy, grant only declared consumers/keys, keep values out of argv/logs/registry/committed config, and do not mistake Compose interpolation for container environment delivery. `[prd 01 §5.4]`
- [ ] Make requested-port conflicts actionable: identify only safe process metadata, refuse implicit takeover, and require a fresh explicit stop/replace decision before claiming ports such as 3000 or 8000. `[prd 01 §5.6]`
- [ ] Prove the selected product contract on representative Laravel 12 + MySQL and Next.js + pnpm projects on Apple Silicon, including edit/reload behavior, backend calls from browser and server components, database persistence, repeated lifecycle operations, test commands, secret non-disclosure, and a measured comparison with the prior host-native baseline. `[prd 01 §8]`
- [ ] Keep generic incumbent-Herd execution outside this phase unless the owner explicitly selects native equivalence. If selected later, it needs a separate PRD and must remain labeled trusted shared-host/lower-isolation; it may not weaken Compose defaults or reuse the WordPress-only unproven adoption claim. `[prd 01 §6, §12]`
