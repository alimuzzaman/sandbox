# Scoped recovery workflow

1. Run `./sb recovery profiles --json` and `./sb recovery plan --json`.
2. Inspect `./sb recovery schedule --json` and `./sb recovery retention --json`; these never activate or delete.
3. Obtain explicit confirmation before protected operations.
4. Use only `./sb recovery` commands—never raw GnuPG, rclone, Docker, SSH, or systemctl.
