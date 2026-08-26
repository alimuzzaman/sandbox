# Remote runtime staging

Remote provisioning, `remote up`, and confirmed remote-service migration stage
the current Sandbox runtime before changing the remote service. Staging has a
bounded 300-second package step and a bounded 300-second SSH upload step.

Progress is written to stderr, so `--json` keeps stdout as one JSON document.
If packaging times out, the remote was not contacted. If the upload times out,
completion is unknown; inspect the remote service before retrying. The command
does not launch a second upload identity automatically.

The upload timeout is reported as `remote_runtime_source_timeout` in the
structured remote-service response.
