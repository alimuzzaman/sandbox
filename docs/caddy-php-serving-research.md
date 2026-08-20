# Caddy and PHP serving research

Status: research complete; no runtime change approved.

## Current Sandbox architecture

Sandbox's default clean-URL provider is its own Caddy proxy. The proxy owns
hostname and route composition and reverse-proxies each instance to a bounded
backend; it does not execute the instance's PHP. See
[`docs/clean-url-default.md`](clean-url-default.md) and
[`sandbox/ingress/adapters/sandbox_caddy.py`](../sandbox/ingress/adapters/sandbox_caddy.py).

The default instance web tiers remain the existing per-instance Compose
profiles: Apache, or nginx in front of a PHP-FPM `wp` service (with the
existing LiteSpeed option). The nginx renderer keeps the WordPress tree at the
same path in the nginx and FPM services and publishes only the nginx port;
[`sandbox/core/_docker.py`](../sandbox/core/_docker.py) is the source of that
composition. The managed-native design likewise keeps nginx or Apache, PHP-FPM,
the database, and cron inside one per-instance boundary; it does not reuse a
host PHP-FPM socket. See [`specs/039-native-runtime-adoption/research.md`](../specs/039-native-runtime-adoption/research.md).

Host-incumbent Caddy adoption is an opt-in ingress adapter. It adds an owned
route fragment only after listener/configuration proof and leaves unrelated
routes untouched; it is not a replacement for the default Sandbox Caddy path.
See [`docs/host-ingress.md`](host-ingress.md) and
[`specs/037-host-ingress-adoption/evidence/system-caddy.md`](../specs/037-host-ingress-adoption/evidence/system-caddy.md).

## Findings and decision

Caddy's `php_fastcgi` is an opinionated FastCGI proxy to a PHP-FPM gateway. It
combines the usual front-controller rewrite with static-file serving when
paired with `root` and `file_server`, and it can target either a TCP endpoint or
a Unix socket. That is a sound pattern for a Caddy process that owns one
application root and one FPM boundary.

FrankenPHP's `php_server` is a different execution model: FrankenPHP embeds
PHP in a Caddy distribution and replaces both the web server and PHP-FPM for
that boundary. Classic and worker modes have different lifecycle and memory
semantics, and a FrankenPHP process shares its PHP thread pool across its
`php_server` blocks.

**Decision: make no runtime change.** The current aggregate Caddy plus
per-instance Apache/nginx/PHP-FPM design already preserves the required
instance ownership and rollback boundaries. Switching the aggregate proxy to
`php_fastcgi` would require it to mount and distinguish every WordPress root,
own every PHP socket, and replace the selected per-instance web tier. Adopting
FrankenPHP would be a new runtime adapter, not a Caddy directive substitution.
Neither change is justified by documentation research alone, and no live
conformance evidence was collected here.

## Future boundary (if revisited)

Any Caddy plus FPM experiment must be **per instance** (or inside one
per-instance OS/container boundary):

- one document root, PHP version/extension set, FPM pool/socket, Caddy config,
  process lifecycle, logs, and health receipt per instance;
- the aggregate Sandbox Caddy may only reverse-proxy to the instance endpoint;
  it must not mount instance roots or execute their PHP;
- socket, config, PID, and writable-state ownership must be attributable to the
  instance and survive drift checks; no host or sibling socket may be reused;
- route, hostname, and TLS mutations remain with the ingress/domain services,
  never with the PHP runtime adapter.

### Shared-ingress prohibition

Do not introduce a shared Caddy serving multiple WordPress roots through one
shared PHP-FPM pool/socket, a host-global PHP-FPM service, or a cross-instance
document-root mount. A shared front proxy is acceptable only as the existing
bounded reverse-proxy layer; PHP execution and writable state remain below the
per-instance boundary. Any exception would need an explicit isolation design
and proof, not an adapter convenience.

### FrankenPHP is a separate prototype

FrankenPHP may be evaluated only as an explicitly selected, separately
registered runtime adapter. It must not become the default Caddy path or an
implicit fallback. A prototype must choose classic versus worker mode and
prove, for each supported PHP version and extension set:

1. WordPress front-controller, static-file, REST, multisite, uploads, and
   error handling parity;
2. CLI, WP-CLI, Composer, and PHPUnit execution/version parity;
3. per-instance process, root, configuration, resource, and credential
   isolation, including worker reset/reload behavior;
4. bounded startup, health, logs, graceful reload, crash recovery, and
   data-preserving rollback;
5. ingress composition without shared sockets, route widening, or host-service
   mutation; and
6. closed CLI/MCP status and redaction behavior for all failures.

## Evidence required before proposing implementation

Before changing runtime semantics, produce a reviewed design/spec and an
explicit adapter registration. Then collect deterministic renderer/unit tests,
the PHP-version and extension matrix, per-instance socket/ownership checks,
WordPress/WP-CLI/PHPUnit conformance, lifecycle/reload/rollback evidence,
resource and isolation adversarial tests, and a live proof on every platform
and ingress combination to be advertised. The proof must include failure and
drift cases and show that unrelated Caddy routes, host services, sockets,
credentials, and writable state remain unchanged. Until those gates exist,
the documented no-change decision stands.

## Official references

- [Caddy PHP serving patterns](https://caddyserver.com/docs/caddyfile/patterns#php)
- [`php_fastcgi` directive](https://caddyserver.com/docs/caddyfile/directives/php_fastcgi)
- [PHP-FPM configuration](https://www.php.net/manual/en/install.fpm.configuration.php)
- [FrankenPHP configuration and `php_server`](https://frankenphp.dev/docs/config/)
- [FrankenPHP classic mode](https://frankenphp.dev/docs/classic/)
- [FrankenPHP worker mode](https://frankenphp.dev/docs/worker/)
- [Migrating from nginx/PHP-FPM to FrankenPHP](https://frankenphp.dev/docs/migrate/)
