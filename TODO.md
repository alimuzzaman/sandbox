# Sandbox TODO

Updated: 2026-08-06. Structure: standing engineering work (§1–§2) runs continuously; product delivery is phased, each phase owned by a standalone PRD under `todo/` (index and decision list in `todo/README.md`). Finished work is deleted, not archived — history lives in git and the spec ledgers.

Sources: `[mail]` = 2026-08-06 live host probes + provider research · `[dns]` = 2026-08-06 Cloudflare zone work · `[ops]` = observed on the remote host · `[prd NN]` = detailed brief in `todo/NN-*/prd.md`.

Direct-delivery research is done (2026-08-06: port 25 open, IP unlisted, host is Contabo not Scaleway). Do not re-run the provider comparison; the open questions that remain are listed in `todo/00-outbound-mail/prd.md` §11.

## 1. Loose ends (hours; found while shipping, none blocking)

- [ ] `hermes.asb.bd` serves the Cloudflare Access 302 for `/robots.txt` — Access runs ahead of Workers, so the deny never reaches it. Accepted: the host is Access-gated, nothing is crawlable. Revisit only if the route ever goes public. `[dns]`
- [ ] Worker route patterns don't match a query string, so `/robots.txt?x=1` falls through to the origin (523 on hostnames with no vhost). Cosmetic — crawlers request it bare. `[dns]`
- [ ] `replay.lenzora.dev` returns 525 (TLS handshake failed at origin) — unrelated to mail, spotted during the robots sweep. `[ops]`

## 2. Platform integrity (continuous)

- [ ] `sandbox/core/_cloudflare.py` upserts address records only — no TXT/CNAME. Blocks every DNS-automating feature, mail first. `[dns → prd 00 §4.4]`
- [ ] Instance mail is a dead end by construction: `_write_mail_muplugin` rewrites From to an invalid no-TLD address so `wp_mail()` fails loudly. Right for a laptop, wrong for a public preview. `[mail → prd 00]`
- [ ] One IP, one reputation, shared by every preview and every permanent site. Needs per-instance rate limiting before sending is switched on widely. `[mail → prd 00 §10]`

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
