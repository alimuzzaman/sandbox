# License status JSON output

Use `license status --json` when automation needs the current license state:

```sh
./sb license status --json
```

The response is a single JSON document. It contains only the existing masked
Elementor hint (`set (…1234)` or `(not set)`) and the non-secret primary
instance/URL. It never includes the raw key or captured license data.

`--json` is intentionally limited to `status`; `set`, `clear`, and
`elementor-sync` keep their existing mutation and redaction rules.
