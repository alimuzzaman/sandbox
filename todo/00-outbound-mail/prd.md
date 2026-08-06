# PRD 00 — Outbound mail from each instance's own domain

Date: 2026-08-06 · Status: Product brief for later Spec-Kit conversion · Owner surfaces: instance mail transport, `sandbox.config.json` / `sandbox.hosting.yml` mail blocks, Cloudflare DNS automation, remote provisioner, mail mu-plugin
Sources: live probes of the remote host 2026-08-06 (port-25 reachability, blocklist status, rDNS, resources) · provider research 2026-08-06 (SES / Scaleway TEM / Postal / maddy / Stalwart / Postfix) · `sandbox/core/_provision.py:85` (`_write_mail_muplugin`, current Mailpit capture) · `sandbox/core/_remote.py:665` (`_caddy_proxy_command`, preview route provisioning) · `sandbox/core/_hosting.py` (permanent hosting manifest) · spec 014 remote hosting

> Standalone brief. Sandbox instances can receive nothing and send nothing today: `wp_mail()` is captured by Mailpit and dies there. Inbound was solved separately on 2026-08-06 (Cloudflare Email Routing, catch-all on all seven zones). This PRD covers **sending only**.

---

## 1. Problem

- Every instance captures mail to Mailpit (`_write_mail_muplugin`) and rewrites the From address to an invalid no-TLD value so `wp_mail()` fails loudly rather than silently. Correct for a laptop; wrong for a publicly resolvable preview a client is looking at, and wrong for a permanent hosted site.
- Anything that depends on a real inbox is therefore untestable end to end: password reset, order/notification mail, plugin licence flows, WooCommerce test checkouts, admin-email verification.
- Permanent hosted sites (`sb host apply`) have the same hole. A production WordPress that cannot send password resets is not production.
- Preview hostnames are unbounded and ephemeral (`staging-792f8c40-templately-staging.asb.bd`, new per `sb preview create`), so any design requiring per-hostname setup before mail works is unusable.
- **Sending from the wrong identity is worse than not sending.** A preview restored from a production snapshot holds real customer rows; the first cron or registration hook mails them from a domain that now authenticates. Silence is the safe default; sending must be opted into.

## 2. Desired outcome

1. An instance sends mail **as its own bound domain** — `wordpress@staging-x.asb.bd` from that preview, `wordpress@lenzora.dev` from that hosted site — authenticated well enough to reach a Gmail inbox.
2. **Zero third-party spend and no per-hostname provisioning.** Mail leaves the Sandbox host directly to the recipient's MX.
3. **Capture stays the default.** Real sending is an explicit per-instance opt-in with a recipient allowlist; local `.tst` instances never send.
4. DNS that sending depends on (SPF, DKIM, DMARC, the mail host records) is created by Sandbox through the Cloudflare API, not by hand.

## 3. Target users and jobs

| User | Job |
| --- | --- |
| Plugin developer | "Trigger a password reset on this preview and read the mail that actually arrived" |
| Client reviewing a preview | "Register on the staging site and receive the confirmation" |
| Operator of a permanent site | "Password resets and notifications leave lenzora.dev and land in the inbox" |
| Agent (MCP) | Same behaviour headlessly: `mail_list` for captured, delivery status for sent |

## 4. Scope

### 4.1 Delivery path — direct, no relay

Verified on the remote host 2026-08-06, and these findings **overturn the earlier assumption that a paid relay was mandatory**:

| Probe | Result |
| --- | --- |
| Outbound TCP/25 → `gmail-smtp-in.l.google.com`, Microsoft MX | **Open** — live `220` banners from both |
| Spamhaus ZEN (IPv4 + IPv6), SpamCop, SORBS | **Not listed** |
| Host | **Contabo** (`vmi3430003.contaboserver.net`) — the remote is only *named* `scaleway-sandbox`; Scaleway's port-25 policy never applied |
| Resources | 11 GiB RAM, 6 cores, 7.8 GiB free, 17 containers, load 0.5 |
| rDNS | Generic hoster PTR on both IPv4 and IPv6 — **must change** |
| Existing MTA / :25 listener | None |

**Postfix as a send-only MTA with OpenDKIM**, bound to the Docker network, delivering to recipient MX itself. ~15 MB. Rejected alternatives, with the reason each lost:

| Option | Why not |
| --- | --- |
| Postal | MariaDB + RabbitMQ + Ruby workers, OOMs under 4 GB — half the host's free memory for delivery tracking nobody asked for |
| Stalwart | ~100 MB and a large settings surface to gain IMAP/JMAP/inbound, which Cloudflare Email Routing already provides |
| maddy | Good fit and clearer docs, but its edge (multi-domain DKIM signing) is a one-file OpenDKIM config here; single maintainer, bursty releases, against Postfix's distro security stream |
| Provider relay (SES/TEM) | Costs money and adds an account for a capability the host already has. Remains the fallback: one `relayhost` line, no architecture change |

### 4.2 What must be true before a single mail sends

Ordered, because the first three must agree exactly or nothing else matters:

1. **rDNS on both IPs** → `mail.asb.bd`. Contabo panel; **the one step Sandbox cannot automate.**
2. **Forward DNS** `mail.asb.bd` A → `212.47.72.49`, AAAA → `2a02:c207:2343:3::1`, both **grey-cloud**. A proxied record returns Cloudflare IPs, forward-confirmed rDNS fails, and the sender reads as forged.
3. **HELO = `mail.asb.bd`** (Postfix `myhostname`).
4. **SPF per sending domain, merged not replaced** — Email Routing's include must survive:
   `v=spf1 include:_spf.mx.cloudflare.net ip4:212.47.72.49 ip6:2a02:c207:2343:3::1 ~all`
5. **DKIM** — keypair per sending domain, `<selector>._domainkey.<domain>` TXT; preview subdomains signed with the parent `asb.bd` key.
6. **DMARC alignment relaxed.** `asb.bd` is `p=reject; sp=reject; adkim=s; aspf=s`. A preview sending as `wordpress@staging-x.asb.bd` signed `d=asb.bd` fails strict alignment and **the zone's own policy rejects it**. Change to `adkim=r; aspf=r`, add `rua=`.
7. **IPv6 posture.** Default egress is IPv6 and receivers are materially stricter there (valid PTR + SPF pass or outright rejection, not junk). Ship `smtp_address_preference = ipv4`; enable v6 once both PTRs are correct and mail is landing.
8. **Never an open relay** — `mynetworks` limited to Docker subnets, no public :25 listener (nothing listens today; the config must not change that).

### 4.3 Instance modes

| Mode | Behaviour | Default for |
| --- | --- | --- |
| `capture` | Today's Mailpit path, unchanged; `mail_list`/`mail_get` read it | Local `.tst`, and **all previews** |
| `send` | Relays to the host MTA; From = `wordpress@<instance-domain>`; recipient allowlist enforced before handoff | Opt-in per instance |
| `send` (unrestricted) | No allowlist — permanent hosted sites only, declared in `sandbox.hosting.yml` | Permanent sites |

The allowlist is a list of recipient domains (`@wpdeveloper.com`, `@gmail.com`). A recipient outside it is captured to Mailpit instead of sent, and the substitution is logged — never silently dropped. Local instances cannot be switched to `send`: `.tst` has no public DNS and nothing to authenticate.

### 4.4 DNS automation

`sb mail setup <domain>` generates the DKIM keypair, then writes DKIM TXT, merged SPF, and DMARC through the Cloudflare API; `sb mail status <domain>` re-reads and reports drift. The existing Cloudflare client (`sandbox/core/_cloudflare.py`) only upserts address records — TXT support is the gap. Reuses the token already configured. Every mutation backs up the prior record set first, as the 2026-08-06 Email Routing cutover did.

### 4.5 Configuration surface

Project (`sandbox.config.json`): `mail: { mode, from, allowRecipients[] }`. Permanent hosting (`sandbox.hosting.yml`): a `mail:` block beside the `robots:` key added 2026-08-06. Both optional; absent means `capture`.

### Non-goals

Inbound mail to instances (Cloudflare Email Routing owns it); mailbox hosting; bulk/marketing sending; per-message delivery tracking and webhooks (Postal's territory — revisit only if a real need appears); IP warmup automation; sending from domains outside the Cloudflare account.

## 5. Flows

**Preview, default:** boot → `capture` → `mail_list` shows the mail. Unchanged from today.
**Preview, sending:** set `mail.mode=send` → apply → WordPress mails `wordpress@staging-x.asb.bd` → MTA signs `d=asb.bd`, delivers → allowlisted recipient receives; non-allowlisted is captured with a log line.
**Permanent site:** `sb mail setup lenzora.dev` (DNS written, verified) → `mail: send` in the manifest → `sb host apply` → password resets deliver.
**Diagnosis:** `sb mail status <domain>` reports each of §4.2's eight conditions as pass/fail with the observed value, so a delivery failure names its own cause instead of requiring a `dig` session.

## 6. States

Per domain: `not configured` · `dns pending` (records written, propagating) · `ready` · `drifted` (a record changed underneath) · `blocked` (rDNS mismatch or listed IP). Per instance: `capture` · `send (allowlisted)` · `send`. Per message when sending is on: `sent` · `captured (recipient not allowlisted)` · `deferred` · `bounced`.

## 7. Data needs

Per-domain DKIM private keys — secrets, so `sandbox.local.yml` + `.env.local` only, never argv, compose env, snapshots, or git. Per-domain selector and record state for drift detection. Postfix queue and logs on the host, readable through the existing bounded log tooling rather than a new stream.

## 8. Dependencies

Cloudflare API token (present, verified); the `_cloudflare.py` TXT gap; Contabo panel access for rDNS (human); `mail.asb.bd` TLS via the Caddy already on the box; remote provisioner for the Postfix role.

## 9. Success metrics

A preview password-reset mail lands in a Gmail **inbox** (not Junk) within 60s, DKIM `pass` + SPF `pass` + DMARC `aligned`, verified against `mail-tester`-style headers. `sb mail status` catches every one of §4.2's failure conditions in a deliberately broken fixture. Zero mail sent to a non-allowlisted recipient from any preview. Host memory overhead under 50 MB.

## 10. Risks

- **Reputation:** cold clean IP → Gmail may Junk early mail; low volume plus marking not-spam corrects it. Microsoft blocks cloud ranges regardless of correctness — SNDS/JMRP signup and the sender-support form are the only levers, and this is the likeliest reason to fall back to a relay.
- **Blast radius of a mistake:** a preview with production data that sends is the failure that matters. Mitigated by capture-default, allowlist, and making `send` an explicit per-instance act — and by never defaulting previews to `send` regardless of how convenient it becomes.
- **Zone-policy coupling:** relaxing DMARC alignment on `asb.bd` is a real (small) security reduction on a zone that also carries `hermes` and the control plane. Relaxed is the DMARC default and `p=reject` still enforces; state it explicitly rather than let it be discovered.
- **Shared-IP coupling:** every instance shares one IP and one reputation. One noisy preview degrades delivery for the permanent sites. Rate limits per instance, and the relay fallback if it ever bites.

## 11. Unresolved decisions (owner input)

1. Mail host name — `mail.asb.bd` proposed. It anchors PTR, HELO, and TLS, and moving it later means redoing all three.
2. DKIM key scope — one key per zone (fewer records, `d=` always the apex) vs per-domain keys for permanent sites (blast-radius isolation). Proposal: per zone now, per domain when a client site demands it.
3. Recipient allowlist default — proposal: the operator's own domains, empty meaning "capture everything", so a misconfigured allowlist fails safe.
4. Does `sb mail setup` relax DMARC alignment automatically, or refuse and print the record for a human? Proposal: refuse — it is a zone-wide security posture change and should not be a side effect of a mail command.
5. Permanent sites: unrestricted `send` from day one, or allowlisted until a first delivery proof exists?

## 12. Rollout

Records and MTA first, sending off. Prove with one throwaway preview to a single allowlisted address, headers checked. Then permanent sites one at a time, watching logs between. `sb mail status` ships before `mail.mode=send` is documented, so the first failure is diagnosable. Rollback is `mode: capture` everywhere plus stopping Postfix — no DNS rollback needed, since SPF/DKIM/DMARC records are inert when nothing sends.
