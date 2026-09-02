# Authority Contract: `wordpress-cache-v1`

## Purpose

`wordpress-cache-v1` is permission to install one bounded cache-routing fragment inside
the selected instance's existing WordPress site. It is not permission to administer a
server, listener, process, network, image, Caddy route, or arbitrary file.

Both common policy and the selected server adapter must accept. Unknown syntax,
ambiguous context, ignored directives, or incomplete path proof is refusal.

## Common Input Boundary

- Name: 1-64 bytes, full match `[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,63}`.
- Source: exactly one `O_NOFOLLOW` regular file or stdin stream.
- Size: 1-262,144 bytes. Read one additional byte to prove oversize.
- File stability: identity, type, owner, size, and modification facts must match before
  and after the bounded read; otherwise refuse.
- Encoding: strict UTF-8; no BOM, NUL, C0/C1 controls except tab/LF/CR, or unterminated
  quoted/token context.
- Bytes are stored and hashed exactly as accepted. Line endings and trailing newline are
  not normalized.
- Name, routine metadata, and exact content pass the existing high-confidence
  secret-redaction/classification boundary before storage. A credential-like name or
  secret-like content match is refused with `fragment_secret_like_input`; the result
  never identifies the matched token, rule, offset, or bytes. False positives fail
  closed and require caller-supplied content without the match. Content is never a
  supported secret carrier.

## Allowed Semantic Authority

The fragment may express only:

1. same-site matching on request method, host, URI, query presence, cookie names,
   user-agent, and file existence;
2. internal routing to a regular cache artifact beneath the selected instance's
   WordPress document root, limited to `wp-content/cache/` or an adapter-approved cache
   subtree;
3. a bounded response marker such as `X-XSpeed-Cache` on the cache-hit path;
4. an access/hit log only beneath `wp-content/uploads/` where the selected web service
   already has the required access;
5. server-native cache lookup, vary, TTL, bypass, purge/tag, and cache-control behavior
   needed to prove a WordPress plugin's cache lifecycle, scoped to the same site;
6. fragment-local variables needed to combine those conditions.

No accepted rule may broaden file access, serve another host, or intercept protected
Sandbox/WordPress control routes.

## Always Forbidden

- server/vhost/listener definitions; address/port/protocol binding;
- `include`, imports, wildcards, dynamic file loads, environment reads, or module loads;
- process user/group, daemon, worker, PID, chroot, privilege, admin, logging-global, or
  binary settings;
- proxy, FastCGI/upstream selection, arbitrary redirects, external URLs, DNS, sockets,
  mail, command/program execution, embedded code, or template evaluation;
- TLS/certificate/key settings, Caddy/Docker proxy settings, clean-URL or host ingress;
- paths outside the active container document root; traversal; ambiguous variables in
  a path; symlink-dependent escape; devices/sockets/FIFOs;
- changes to `/wp-admin`, `/wp-login.php`, `/wp-json` health/readiness endpoints,
  Sandbox autologin/bridge/MU-plugin paths, error handling, PHP dispatch, or front
  controller fallback;
- request/response body disclosure, cookies or authorization values in logs/headers,
  arbitrary caller-chosen response headers, or unbounded marker values;
- native directives not explicitly accepted by the adapter revision.

Comments are data, not instructions. They do not widen authority and are not returned in
routine evidence.

## nginx v1 Subset

The nginx adapter accepts a parsed server-context subset sufficient for plugin static
cache rules such as xSpeed's emitted snippet:

- `set` of a fragment-local variable from allowlisted request variables, captures, and
  bounded literal text;
- server-context `if` with a single allowlisted comparison, regex, query/cookie/UA/URI
  predicate, or regular-file existence check; the body may contain only approved `set`
  or the one internal cache `rewrite`;
- `rewrite` only to an instance-document-root-relative cache artifact, with `last`;
- one or more `location ^~` blocks only for approved cache subtrees;
- inside those locations: `internal`, restricted `add_header`, restricted `access_log`,
  and static-file cache response controls accepted by the adapter revision.

Parsing requirements:

- Tokenize quotes, escapes, comments, variables, braces, and directive terminators; a
  line regex is not a parser.
- Assignment to native/reserved/Sandbox variables is forbidden. Custom variables must
  be defined and consumed inside the complete candidate.
- Only documented request variables used by the accepted predicates are readable.
- Regexes are bounded, compile under the exact-image validator, and cannot interpolate
  path or credential data into a response/log.
- `add_header` permits an adapter allowlist of non-sensitive cache evidence names and a
  bounded static value. It cannot set `Set-Cookie`, authorization/security/TLS headers,
  redirects, CORS, or Sandbox-owned headers.
- `access_log` must be a literal path proven beneath this instance's
  `wp-content/uploads/`; variable paths and custom formats are forbidden.
- The renderer places fragments before the existing `location /` front controller and
  preserves every protected base route. Duplicate/conflicting locations or variable
  definitions across the complete set are refused.

Native `nginx -t` acceptance is necessary but not sufficient; common/adapter policy and
complete-candidate inclusion proof must already pass.

## OpenLiteSpeed v1 Subset

The OpenLiteSpeed adapter accepts only its reviewed vhost-local cache/rewrite subset:

- rewrite enablement and WordPress-cache rewrite conditions/rules;
- cache lookup and cache-control environment flags used for hit, bypass, vary, TTL,
  tag, and purge behavior;
- context rules limited to the existing WordPress document root and approved cache
  subtree;
- bounded cache-hit response evidence supported by the exact active image.

The adapter rejects server/listener/admin/global cache settings, arbitrary contexts,
external processors, script handlers, MIME/module changes, realm/auth settings,
vhost-root/docroot changes, unrestricted `.htaccess` loading, and any directive not in
its versioned catalog.

The fragment is rendered into an adapter-owned complete vhost generation. It is not
appended to the live WordPress `.htaccess`, does not overwrite plugin-owned `.htaccess`
bytes, and cannot enable broader recursive override behavior. The exact-image isolated
boot must prove:

- the candidate vhost is selected;
- each named fragment marker is reachable exactly once;
- a synthetic cache route produces the adapter canary behavior;
- protected origin/PHP and health paths remain reachable;
- no directive was ignored or downgraded.

If the image cannot expose a deterministic vhost inclusion point or canary proof, the
adapter returns `validation_capability_unavailable` before live mutation.

## Complete-Set Conflict Rules

Regardless of individual acceptance, the complete set is refused when it has:

- duplicate normalized names, custom variables, locations, contexts, cache keys, or hit
  marker ownership;
- ordering-dependent authority not represented by deterministic name order;
- one fragment routing into another fragment's writable/control path;
- mutually inconsistent bypass/vary/cache rules that make protected behavior ambiguous;
- combined native validation failure or missing inclusion marker.

## Policy Result

Policy returns content-free evidence:

```json
{
  "status": "accepted",
  "authority": "wordpress-cache-v1",
  "common_policy_revision": "wordpress-cache-v1/common/1",
  "adapter_policy_revision": "wordpress-cache-v1/nginx/1",
  "content_id": "sha256:...",
  "checks_digest": "sha256:..."
}
```

A refusal replaces `accepted` with a stable code and safe rule category. It never
returns a line, token, regex, path, excerpt, or native diagnostic derived from content.

## Change Control

Adding an allowed directive, variable, header, path class, cache behavior, or adapter is
a security-policy change. It requires focused negative tests, docs, human review, exact
image validation, live isolation evidence, and a policy revision bump. Relaxation cannot
ship as an unversioned parser tweak.
