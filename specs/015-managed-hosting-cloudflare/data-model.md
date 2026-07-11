# Data Model

## HostingManifest

`version`, `project`, and a non-empty `environments` map. Each environment owns one
Compose web service, deployment policy, host routes, and Cloudflare policy.

## HostRoute

Normalized hostname; `serve` routes to the environment service, while `redirect`
requires an HTTPS target. Wildcards are valid only for `serve` routes.

## HostedEnvironmentState

Remote-only state keyed by project/environment: Compose project name, assigned loopback
port, certificate metadata, and last known route/DNS values for rollback.

## WordPressFilesystemPolicy

For an opt-in hosted WordPress environment, the Apache runtime identity owns the
WordPress application tree and the persistent uploads volume. A root-only permissions
job restores this invariant before the non-root WordPress initializer runs, including
after an image replacement or when an existing named uploads volume was created by root.

## CloudflareZoneChange

Zone identifier, normalized hostname, A/AAAA desired records, proxy state, and whether
the zone requires an acknowledged Full (strict) mode change.
