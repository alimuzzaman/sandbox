# Optional WP option probes

Use the explicit probe mode when an absent option is expected:

```sh
./sb wp --allow-missing -- option get xspeed_cache
```

An absent option returns one JSON document:

```json
{"present":false,"value":null}
```

Transport, runtime, and other WP-CLI failures are not converted to “missing.”
The flag is also available for an explicit synchronous plugin cleanup:

```sh
./sb wp --allow-missing -- plugin deactivate valid-plugin absent-plugin
```

For this form Sandbox performs a read-only `plugin list` preflight, skips only
absent or already-inactive slugs, deactivates the remaining slugs, and returns
one typed JSON result. An ambiguous preflight or a real deactivation failure
stops without converting it to success. The `--all` and option-before-slug
forms are rejected because Sandbox cannot safely identify the requested set.
