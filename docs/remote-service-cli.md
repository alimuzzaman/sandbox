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
to apply either operation.
