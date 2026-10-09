# Quickstart: Remote Runtime Revision Coexistence

Offline (always):
1. `/opt/homebrew/bin/python3 -m unittest tests.test_remote_runtime_verdict tests.test_remote_runtime_pins tests.test_remote_runtime_refusal tests.test_remote_registration_lock tests.test_architecture_boundaries` → OK.
2. `./sb selftest` → OK.

Live (disposable remote; follow the remote install protocol, never Lenzora's remote without approval):
1. From checkout M: `./sb remote service migrate R --confirm`; `./sb remote service status R --json` shows `compatibility.state=compatible` and `control_protocol.installed`.
2. From checkout F (unrelated Python change, same protocol): `./sb remote service status R --json` → `compatible`, `runtime_revision_state=mismatch`; a remote test job is accepted.
3. `SANDBOX_STRICT_RUNTIME=1 ./sb remote service status R` from M → pin listed; from F → refused `remote_runtime_revision_mismatch`, no pin.
4. From F: `./sb remote service migrate R` → plan lists M's pin; `--confirm` refuses with zero writes; `--confirm --break-pin <holder>` proceeds; M's next strict call reports the breaker.
5. While a hosted apply runs on R, `./sb remote list` and `./sb remote service status OTHER` return immediately.
