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

A remote serves one runtime revision. When the installed revision differs from
this checkout's, `migrate --plan` (and the confirmed result) adds
`replaces_revision` and a warning: any other checkout or deploy pinned to the
installed revision fails its runtime check until it migrates again. Check with
whoever owns that pin before confirming.

`status` bounds each user-systemd probe and returns `probe_state` (`complete`,
`partial`, or `unavailable`) with a stable `probe_error` when evidence is
incomplete. SSH transport failures return a degraded JSON result and a nonzero
exit. Confirmed migrate, `remote up`, cleanup, and stop operations refuse
incomplete status before uploading source or changing the service.

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

## Installed source revision (spec 072)

A confirmed migrate (and provision or `remote up`, which share the
installer) writes `$HOME/sandbox/runtime/source-revision.json` on the remote
after the unit is active: `{"schema_version":1,"source_revision":"<40-hex>"}`,
mode 0600. The write is best effort and never fails a finished install.
`sb remote service status NAME --json` reads it in the same ssh call and
reports `installed_source_revision` (`null` when the receipt is missing or
malformed, or the remote was installed before spec 072). The controller also
records the revision it installed locally. Both feed controller auto-update;
see [controller-auto-update.md](controller-auto-update.md).
