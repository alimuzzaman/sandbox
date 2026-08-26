# Sandbox home JSON output

Use the read-only form when a script needs the machine-local state locations:

```sh
./sb home --json
```

The single JSON document includes the Sandbox base, runtime directory, config
path, and the feedback-store root (plus existence flags). It does not read or
print feedback contents, credentials, or cursor values. `sb home <dir>` remains
the explicit relocation command and is unchanged.
