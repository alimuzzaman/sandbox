# Contract: Runtime-Aware Remote Deploy

`sb deploy --project-dir DIR --remote NAME [--ensure] [--expose] [--domain HOST]
[--plugin-slug SLUG] --json` remains the public command shape.

For WordPress, current behavior is unchanged: ensure, activate the selected plugin,
route `wordpress_port`, and update `home`/`siteurl` when exposed.

For `kind: compose`, ensure invokes the existing remote lifecycle. Exposure routes the
returned positive `http_port`; no plugin is activated and no WordPress command runs.
If `--plugin-slug` is explicitly supplied for a generic project, the command fails
before connecting to the remote. A missing/non-positive generic `http_port` causes a
structured deploy error and no route is configured.

The MCP `remote_deploy` accepts the same arguments and returns the same result object.
Its capability preflight is selected from the project descriptor rather than fixed to
WordPress.
