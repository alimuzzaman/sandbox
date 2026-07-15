# Contract: CLI and MCP

CLI root command: `sb recovery`. Actions: `profiles`, `plan`, `create`, `list`, `verify`,
`restore`, `retention`, and `schedule`. JSON output uses one stable result envelope. Create reads
the passphrase from `RECOVERY_PASSPHRASE` in the approved inherited environment channel and
fails if it is absent or empty. Restore defaults to plan; apply requires
`--confirm`. Retention defaults to plan; deletion requires `--confirm`. Schedule defaults to
plan; its disabled unit carries the selected profiles and optional remote target; activation/removal require `--confirm`.

CLI capture accepts repeated explicit materialized --artifact NAME=PATH inputs and requires a
set ID plus profile selection. MCP capture accepts the equivalent `backup_id`, `profiles`, and
`artifacts` map; neither transport accepts a passphrase or discovers host paths implicitly.

MCP exposes corresponding tools with structured arguments and never accepts passphrases. A
mutating MCP tool can request an operation only when the server process already owns the approved
secret channel and the same confirmation/service gates pass.
