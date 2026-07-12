# Contract: CLI and MCP

CLI root command: `sb recovery`. Actions: `profiles`, `plan`, `create`, `list`, `verify`,
`restore`, `retention`, and `schedule`. JSON output uses one stable result envelope. Create reads
the passphrase from `RECOVERY_PASSPHRASE` in the approved inherited environment channel and
fails if it is absent or empty. Restore defaults to plan; apply requires
`--confirm`. Retention defaults to plan; deletion requires `--confirm`. Schedule defaults to
plan; activation/removal require `--confirm`.

MCP exposes corresponding tools with structured arguments and never accepts passphrases. A
mutating MCP tool can request an operation only when the server process already owns the approved
secret channel and the same confirmation/service gates pass.
