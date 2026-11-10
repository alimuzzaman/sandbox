# Feature Specification: Tailnet-Only Hostnames

**Feature Branch**: `feat/host-tailnet-name`

**Created**: 2026-11-10

**Status**: Draft

**Input**: User description: "make it reusable tool in sandbox cli, we will use it more often" (owner, relayed by the xspeed-dev orchestrator). Proposed shape: `sb host tailnet-name add <fqdn> --remote R --upstream HOST:PORT [--json]`, which (1) creates a DNS-only A record for the name pointing at the remote's tailnet IPv4 when absent, accepting an identical existing record and refusing a different or proxied one; (2) gets a DNS-01 certificate; (3) installs it on the remote; (4) writes an nginx server block bound only to the tailnet IP, with a redirect on port 80 and an HTTPS proxy to the upstream on port 443, never a default server; (5) checks that no jobs or host transactions are in flight, tests the nginx configuration (removing the new file on failure) and reloads under the edge lock; (6) verifies that HTTPS answers over the tailnet and that the public address refuses the name. Also `list` (names, upstream, certificate expiry), `remove` (block, certificate, then DNS record, each recorded) and `renew`. Every step can be rerun safely. The Cloudflare token reaches only the child process that needs it, through Sandbox's secrets broker, and never appears in arguments, configuration, output, logs or feedback. No zone is hard-coded.

## Clarifications

### Session 2026-11-10

No question was put to the owner: the brief fixed the behaviour and left the
choices below to the implementer ("pick in the spec", "decide in the spec").

- Q: Which certificate client: an external ACME client or one built into Sandbox? → A: The external `lego` client on the controller, run by the secrets broker, issuing from a signing request made on the remote; Sandbox has no cryptography dependency to sign ACME requests itself (implementer choice).
- Q: Where are the zones, the token reference and the contact address configured? → A: A `tailnet_names` block in the controller's machine configuration (`sandbox.local.yml`): an allow-list of zones, the broker source and key names, the contact address and an optional ACME directory; none of it can be given on the command line except the ACME directory for staging (implementer choice).
- Q: Which address does the public refusal check probe? → A: The remote's registered SSH host when it is, or resolves to, a public IPv4 address; `--public-address` overrides it; when no public address is known the check is reported as not run and the result is not ok (implementer choice).
- Q: What does `add` do when the name is already published with another upstream? → A: It refuses with `tailnet_name_upstream_differs` unless `--replace-upstream` is given, in which case only the server block changes (implementer choice).
- Q: Is the result ok when only the tailnet probe fails (for example, the controller is off the tailnet)? → A: No: the result is not ok with `tailnet_probe_failed`, the name stays published, and `add` can be rerun to repeat only the verification (implementer choice).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Publish an app on a tailnet-only HTTPS name (Priority: P1)

An operator has an app listening on a remote (a mail catcher, an admin panel,
an internal dashboard) and wants teammates on the tailnet to reach it at a
normal HTTPS name, such as `mail.example.test`, with a trusted certificate,
while nobody on the public internet can reach it under that name. Today this
takes a hand-run DNS script, a hand-run certificate client, copying files to
the server, hand-writing a web server block and reloading by hand. The
orchestrator has done it four times.

**Why this priority**: This is the whole feature; every other story maintains
what this one creates.

**Independent Test**: Against a remote with fakes for the DNS provider, the
certificate authority and the server, run `add` for a placeholder name and
confirm the record, certificate, server block, reload and both verification
probes happen in order, and that the result reports each step.

**Acceptance Scenarios**:

1. **Given** a configured zone, a remote with a recorded tailnet address and
   an app listening at an upstream address on it, **When** the operator runs
   `add <name> --remote R --upstream HOST:PORT`, **Then** the name resolves to
   the remote's tailnet address without the DNS provider's proxy, a trusted
   certificate for the name is installed on the remote, the remote's web
   server answers HTTPS for the name only on its tailnet address and proxies
   to the upstream, and the result reports every step as done.
2. **Given** the name already has an identical DNS-only record pointing at the
   tailnet address, **When** `add` runs, **Then** the record step reports it
   as already present and the remaining steps continue.
3. **Given** the name already has a record pointing elsewhere, or a proxied
   record, **When** `add` runs, **Then** it refuses before any change, names
   the conflict, and never changes the record.
4. **Given** `add` already completed for a name, **When** it runs again with
   the same upstream, **Then** every step reports "already done" and the web
   server is neither rewritten nor reloaded.
5. **Given** `add` stopped part-way (interrupted, network loss, a refused
   step), **When** it runs again, **Then** it skips the steps already done and
   completes the rest.
6. **Given** the web server's configuration test fails with the new block in
   place, **When** `add` reaches that step, **Then** the new block is removed,
   the running web server is not reloaded and keeps serving exactly as before,
   and the result reports the failure.
7. **Given** the verification finds that the public address presents a
   certificate for the name, **When** `add` reaches verification, **Then** it
   removes the block again, reloads, and reports the exposure as a failure.

---

### User Story 2 - See what is published (Priority: P2)

The operator lists the tailnet names on a remote to see each name, its
upstream, and when its certificate expires.

**Why this priority**: Certificates expire after about 90 days; without a list
the operator cannot tell which ones need attention.

**Independent Test**: With two names recorded and fake certificates with known
expiry dates on the remote, `list` returns both names, their upstreams and
their expiry dates, and flags the one inside the renewal window.

**Acceptance Scenarios**:

1. **Given** names published on remote R, **When** the operator runs
   `list --remote R`, **Then** each name appears with its upstream, its
   certificate's expiry date, its days remaining, and whether the name's
   server block and certificate are present on the remote.
2. **Given** a name recorded by this controller whose server block is missing
   on the remote, **When** `list` runs, **Then** the name is shown as drifted
   rather than hidden.

---

### User Story 3 - Renew certificates (Priority: P2)

The operator, or a scheduled routine, renews the certificates that are close
to expiry, without touching the ones that are not.

**Why this priority**: Without renewal every name stops working after about
90 days.

**Independent Test**: With one certificate inside the renewal window and one
outside it, `renew --all` reissues only the first, installs it, tests and
reloads the web server, and verifies the name.

**Acceptance Scenarios**:

1. **Given** a name whose certificate expires within 30 days, **When** the
   operator runs `renew <name> --remote R` (or `renew --all --remote R`),
   **Then** a new certificate is issued, installed, the web server is tested
   and reloaded, and HTTPS on the tailnet presents the new certificate.
2. **Given** a name whose certificate has more than 30 days left, **When**
   `renew` runs without `--force`, **Then** it reports "not due" and changes
   nothing.
3. **Given** a renewal whose new certificate fails the web server's
   configuration test, **When** renewal reaches that step, **Then** the
   previous certificate files are restored and the web server keeps serving
   with them.

---

### User Story 4 - Remove a name (Priority: P3)

The operator retires a name: the server block goes, then the certificate, then
the DNS record, and each removal is recorded so a rerun finishes what an
interrupted run started.

**Why this priority**: Removal is needed to keep the zone and the server tidy,
but it is rarer than adding.

**Independent Test**: For a published name, `remove` deletes the block (with a
configuration test and reload), deletes the certificate files, deletes the DNS
record, and records each step; rerunning it reports everything as already
removed.

**Acceptance Scenarios**:

1. **Given** a published name, **When** the operator runs
   `remove <name> --remote R`, **Then** the block is removed and the web server
   reloaded, the certificate files are removed, the DNS record is deleted, and
   the result lists each step.
2. **Given** the DNS record for the name now points somewhere other than the
   remote's tailnet address, or is proxied, **When** `remove` reaches the DNS
   step, **Then** it leaves the record alone and reports that it no longer
   owns it.
3. **Given** a removal that stopped after the block was removed, **When**
   `remove` runs again, **Then** it skips the block and finishes the
   certificate and DNS steps.

---

### Edge Cases

- The remote has no recorded tailnet address, the recorded address is not a
  tailnet address, or the remote does not currently hold it: refuse before
  any change.
- The remote's front door is not the host-incumbent web server (a Caddy
  front door): refuse before any change; this feature manages only that web
  server's own Sandbox files.
- The name is not inside a configured zone, is the zone apex, contains a
  wildcard, or is not a valid lowercase hostname: refuse before any change.
- Another server block on the remote, not written by this feature, already
  serves the name: refuse before any change.
- The upstream is not an IP address and port, or is a public address: refuse.
- A durable job is running on the remote, or another Sandbox host transaction
  holds one of its locks: refuse with a busy code; never wait.
- The broker cannot provide the token (source or key missing, broker refusal):
  refuse before any change, without the token's value in the message.
- The DNS provider or certificate authority is unreachable or rate-limits:
  report the failed step; already-completed steps stay recorded for the rerun.
- The controller is not on the tailnet, so the tailnet probe cannot connect:
  the result is not ok with `tailnet_probe_failed`; the published name stays,
  because the public check is what protects against exposure.
- The same name is published twice with a different upstream: `add` refuses
  with `tailnet_name_upstream_differs` unless `--replace-upstream` is given,
  which rewrites only the server block.
- The DNS record exists but the zone lists more than one A record for the
  name: refuse; never pick one.

## Requirements *(mandatory)*

### Functional Requirements

**Command surface**

- **FR-001**: Sandbox MUST offer `host tailnet-name` with the actions `add`,
  `list`, `renew` and `remove`, each taking `--remote` and an optional
  `--json` that prints one machine-readable result with a stable refusal code.
- **FR-002**: `add` MUST take the full hostname and `--upstream HOST:PORT`,
  where the host is an IPv4 address that is loopback, private or in the
  tailnet range, or `localhost`; any other upstream is refused.
- **FR-003**: Every refusal that does not depend on the remote, the DNS
  provider or the certificate authority (invalid name, name outside the
  configured zones, invalid upstream, missing configuration, unsupported front
  door) MUST happen before any network call or change.

**Configuration and secrets**

- **FR-004**: The zones a controller may publish under, the reference to the
  DNS provider token (a broker source and key name, never a value), the
  certificate contact address and the ACME directory MUST come from the
  `tailnet_names` block of the controller's machine configuration. No zone is
  built into Sandbox. Only the ACME directory may be overridden on the command
  line (for a staging authority).
- **FR-005**: The DNS provider token MUST reach only the child processes that
  call the DNS provider or the certificate authority, through Sandbox's
  secrets broker. It MUST never appear in any command line, configuration
  file, state file, printed or `--json` output, log, audit record or feedback
  report.
- **FR-006**: Children that receive the token MUST be fixed programs chosen by
  Sandbox, never an operator-supplied command.

**DNS step**

- **FR-007**: The DNS step MUST look up the zone and the exact-name address
  records read-only first, and create a DNS-only record pointing at the
  remote's tailnet address only when none exists.
- **FR-008**: An existing single record with the same address that is not
  proxied MUST count as success. A record with another address, a proxied
  record, or more than one record MUST be a refusal, and Sandbox MUST never
  change or delete such a record.

**Certificate step**

- **FR-009**: The certificate MUST be issued by a publicly trusted ACME
  authority through a DNS-01 challenge, because a tailnet-only name cannot
  pass an HTTP challenge from the public internet. The ACME client is the
  external `lego` client on the controller; when it is missing Sandbox refuses
  with `tailnet_name_acme_client_missing` before any change.
- **FR-010**: The certificate's private key MUST be generated on the remote
  and never leave it; only a signing request leaves the remote and only the
  public certificate chain comes back.
- **FR-011**: The installed certificate chain MUST be world-readable and the
  private key readable only by the system administrator account, each in a
  directory dedicated to the name.

**Web server step**

- **FR-012**: The server block MUST listen only on the remote's tailnet
  address: on port 80 it redirects to HTTPS with a permanent redirect that
  keeps the method; on port 443 it serves TLS and HTTP/2 and proxies to the
  upstream with websocket upgrade, unbuffered responses and a 600-second read
  timeout.
- **FR-013**: The server block MUST never declare itself the default server
  for any address and port.
- **FR-014**: The block MUST be written only to a Sandbox-owned file dedicated
  to the name; Sandbox MUST never edit the incumbent web server's own files.

**Gate, test and reload**

- **FR-015**: Before any change to the web server, Sandbox MUST confirm that
  no durable job is active on the remote and that none of Sandbox's host
  transaction locks (hosting proxy, edge web server, container address pool)
  is held, and refuse with a busy code otherwise.
- **FR-016**: The configuration test, the reload and any restore MUST run
  while holding the edge web server lock. If the test fails, the new or
  changed files MUST be put back to their previous state and the web server
  MUST NOT be reloaded.

**Verification**

- **FR-017**: After a reload, Sandbox MUST verify that an HTTPS request for the
  name to the remote's tailnet address completes a TLS handshake with a
  certificate valid for the name and receives an HTTP response.
- **FR-018**: Sandbox MUST verify that a connection to the remote's public
  address with the name as the TLS server name does not complete a handshake
  presenting a certificate valid for the name. If it does, Sandbox MUST remove
  the block, reload, and report an exposure failure. The public address is the
  remote's registered SSH host when it is, or resolves to, a public IPv4
  address, or the `--public-address` given; without one the check is reported
  as not run and the result is not ok.
- **FR-018a**: When the tailnet probe fails, the result MUST be not ok with
  `tailnet_probe_failed`; the name stays published and a rerun of `add`
  repeats only the verification.

**State, idempotency and recovery**

- **FR-019**: Each step MUST check its own completion first and skip itself
  when already done, so a rerun after any interruption completes the
  remaining steps without repeating finished ones.
- **FR-020**: The controller MUST record, per remote and name, the upstream,
  the tailnet address, the zone, the DNS record's identity, the certificate's
  expiry and the outcome of each step, written atomically after each step.
- **FR-021**: `list` MUST report each recorded name with its upstream,
  certificate expiry, days remaining, and whether the block and certificate
  are actually present on the remote, marking any mismatch as drift.

**Renewal**

- **FR-022**: `renew` MUST reissue a certificate only when it expires within
  30 days, unless `--force` is given, and `--all` MUST renew every due name
  on the remote. A renewed certificate that fails the configuration test MUST
  be rolled back to the previous files.

**Removal**

- **FR-023**: `remove` MUST remove, in order, the server block (with test and
  reload under the gate), the certificate files, and then the DNS record,
  recording each step. It MUST delete the DNS record only when it still has
  the recorded identity, the remote's tailnet address and no proxy.

**Documentation**

- **FR-024**: The documentation MUST state that an app behind a tailnet name
  that checks the HTTP Host header (for example the Sandbox panel's
  DNS-rebinding guard) needs its own allow-list entry for the name; this
  feature does not change any app.

### Key Entities

- **Tailnet name**: one hostname published on one remote; attributes: name,
  zone, remote, tailnet address, upstream, DNS record identity, certificate
  expiry, per-step outcomes, timestamps.
- **Tailnet-name configuration**: the controller's allowed zones, the broker
  reference for the DNS provider token, and the certificate contact address.
- **Step outcome**: for each of DNS, certificate, install, server block,
  reload and verification: done, skipped (already done), refused or failed,
  with a code.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator publishes a new tailnet-only name with one command,
  replacing the five-part manual procedure, and the name answers HTTPS on the
  tailnet in under five minutes (most of it certificate issuance).
- **SC-002**: Rerunning `add` on a fully published name changes nothing on the
  remote, the DNS provider or the certificate authority, and finishes with
  every step reported as already done.
- **SC-003**: In 100% of tested failure points (DNS refusal, certificate
  failure, configuration test failure, public exposure, interruption between
  any two steps), the running web server keeps serving the remote's existing
  sites and a rerun completes or cleanly reports the remaining work.
- **SC-004**: Across the test suite, a known marker token value appears in
  none of the command lines, files, outputs, logs or records produced.
- **SC-005**: Zero published names are reachable under their name through the
  remote's public address.

## Assumptions

- The first remote is a host whose incumbent nginx already owns public 80/443
  and has a 443 default server that rejects unknown TLS names; this feature
  uses the nginx front door's existing file ownership and lock conventions.
- The controller is on the same tailnet as the remote, so it can run the
  tailnet verification probe directly.
- The `lego` client is installed on the controller by the operator (for
  example with Homebrew); Sandbox does not download or install it.
- The DNS provider is Cloudflare, which is the only provider Sandbox's hosting
  supports today; the token needs zone read and DNS edit permission for the
  configured zones.
- Scheduling renewal from a routine is a later step; this feature provides
  `renew --all` for such a routine to call.
- Teams using this feature have the Host-header allow-list for the Sandbox
  panel available from separate work; this feature only documents the need.
