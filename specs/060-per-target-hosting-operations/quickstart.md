# Quickstart: validate per-target hosting operations

Use a disposable remote. `xcloud-london` only after the remote install
protocol (no active Lenzora deploy, `sb remote service migrate --confirm`,
repin, `runtime_revision_state: match`) and owner approval.

1. **Unit and contract tests**:
   `.cli-venv/bin/python -m unittest tests.test_hosting_coordination_program tests.test_hosting_coordination_client tests.test_hosting_target_lease tests.test_hosting_holds tests.test_hosting_build_cap tests.test_hosting_state_partition tests.test_hosting_conversion tests.test_hosting_concurrency tests.test_hosting_retained_refusals tests.test_remote_runtime_protocol_shapes tests.test_architecture_boundaries`
2. **Conversion**: `./sb host convert-state --remote R` (plan), then
   `--confirm`. Every prior `sb delivery inspect` request id still resolves.
3. **Different targets in parallel**: from two shells, apply two projects
   on R within 5 s. Both succeed. Builds start within 10 s of admission.
4. **Same target from two controllers**: use a second `SANDBOX_HOME` with its
   own checkout. Run once with `--wait 120` (it waits and names the holder)
   and once with `--wait 0` (immediate `target_busy`, visible in
   `sb delivery inspect`).
5. **Holds**: run `host hold claim --purpose test`. `host operations` from
   the second home shows it. An apply without the hold id waits. Apply with
   `SANDBOX_HOLD_ID` is admitted. A renew past 4 h is refused. A release from
   the other home needs `--break-hold --reason`.
6. **Cap**: with `remote build-cap R` at 2, start three builds. The third
   waits or refuses `build_cap_reached`.
7. **Lost holder**: suspend (SIGSTOP) a holder past 90 s while another apply
   waits, then resume it. Expected: the waiter is admitted only after the
   phase has ceased and the 054 fences clear. The resumed holder records
   `effect_unknown` (`lease_lost`) and makes no further forward effects.
8. **Authority down**: rename the remote capability marker on the disposable
   remote. Every mutation then refuses `lease_authority_unavailable` within
   15 s.
