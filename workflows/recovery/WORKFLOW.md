# Scoped recovery workflow

1. Run `./sb recovery profiles --json` and `./sb recovery plan --remote R --json` to review the profile scope and target inventory.
2. For a server-first capture, confirm the registered remote is current, then broker `SANDBOX_RECOVERY_DB_PASSWORD` and run `./sb recovery capture --remote R --backup-id B --profile amarsonar-bangla-prod --confirm --json`. This path does not need Drive or a passphrase.
3. Poll `./sb recovery status --remote R --backup-id B --json`; inspect `./sb recovery list --remote R --json` for server captures and legacy archives. Keep complete unpromoted captures until promotion.
4. When ready to publish, configure the reviewed destination and inherited `RECOVERY_PASSPHRASE`, then run `./sb recovery promote --remote R --backup-id B --confirm --json`. Verify the manifest and retained status.
5. Run `./sb recovery retention --remote R --json` to review server retention. Only retire a promoted, failed, or incomplete capture with `./sb recovery retention --remote R --backup-id B --confirm --json` after reviewing the candidate.
6. For the existing Drive-set retention review, omit `--remote`. Obtain explicit confirmation before protected operations.
7. Use only `./sb recovery` commands—never raw GnuPG, rclone, Docker, SSH, or systemctl.
