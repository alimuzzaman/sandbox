# Remote service CLI

The `service` action has its own required subcommand. Use one of these forms:

```sh
./sb remote service status NAME --json
./sb remote service diagnostics NAME --processes --json
./sb remote service migrate NAME --plan --json
./sb remote service migrate NAME --confirm --json
./sb remote service stop NAME --confirm --json
```

`status` and `diagnostics` are read-only. `migrate` and `stop` are protected
mutations; `migrate --plan` is the no-write preview and `--confirm` is required
to apply either operation. A confirmed migration stages the current source, then
refreshes the immutable, owner-scoped image staging helper for that exact source
revision before it changes or restarts the user service. Helper validation failure
therefore leaves the existing service untouched.

Source archives may contain symlinks only when parent-relative lexical
resolution stays inside the archive root. Tar hardlinks use archive-root
semantics and must name a direct regular archived file. Absolute links,
escaping links, and link chains fail before publication. Upload refusal
reports a bounded phase and numeric code; it does not echo archive paths or
remote command output. An indeterminate publication cleanup must be inspected
before retry; its bounded stage ID identifies the preserved reconciliation
evidence without exposing a remote filesystem path.

The runtime archive omits only the two repository-authoring links
`skills/speckit-prd-refine/SKILL.md` and
`skills/speckit-prd-validate/SKILL.md`. They point outside the repository and
are not part of the installed runtime. Any other escaping archive link remains
a hard failure.
