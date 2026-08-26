# `sb wp` passthrough

`sb wp` already is the WP-CLI executable boundary. Pass the WP-CLI command
after the separator; do not repeat `wp`:

```sh
./sb wp -- --require=FILE eval-file SCRIPT.php
```

If the first passthrough token is another `wp`, Sandbox rejects it before
starting the runtime and reports the correct spelling. This prevents a
misleading missing-file or plugin error from hiding an argument mistake.

## `wp eval` and PHP namespaces

The `--` separator ends Sandbox options. After it, the expression is passed
unchanged to WP-CLI. Use one PHP namespace separator in a shell single-quoted
expression; do not add a second escaping layer for Sandbox:

```sh
./sb wp -- eval 'echo \XSpeed\Cache::enabled() ? "on" : "off";'
```

Shell single quotes keep those backslashes literal. If an expression contains
more shell quoting or spans multiple lines, put it in a PHP file and use the
same passthrough boundary instead:

```sh
./sb wp -- eval-file /path/to/check.php
```

Sandbox does not rewrite PHP namespaces. A doubled separator is sent to PHP as
written and can produce a parse error; that is an expression-quoting error, not
a runtime or plugin failure.

## Noninteractive WP-CLI help

The managed WordPress image does not require a pager binary. For a passthrough
`help` command, Sandbox adds WP-CLI's `--no-pager` switch unless the command
already includes `--pager` or `--no-pager`:

```sh
./sb wp -- help w3-total-cache option set
```

This keeps help output bounded and usable in shells, jobs, and MCP calls without
changing other WP-CLI commands.

For `eval` parse errors, Sandbox preserves the PHP parse diagnostic and removes
only the duplicate generic WordPress critical-site wrapper. Runtime/plugin
fatals and non-`eval` commands keep their original diagnostics.
