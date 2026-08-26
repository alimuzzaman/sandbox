# `sb wp` passthrough

`sb wp` already is the WP-CLI executable boundary. Pass the WP-CLI command
after the separator; do not repeat `wp`:

```sh
./sb wp -- --require=FILE eval-file SCRIPT.php
```

If the first passthrough token is another `wp`, Sandbox rejects it before
starting the runtime and reports the correct spelling. This prevents a
misleading missing-file or plugin error from hiding an argument mistake.
