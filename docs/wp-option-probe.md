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
The flag is limited to `option get KEY` and performs no write.
