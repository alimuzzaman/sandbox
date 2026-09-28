# Primary external references

Inspected 8 September 2026. These support narrow format/dispatch facts, not claims that this review ran a particular binary. Main-branch URLs can change; version-pinned sources are identified separately.

| Source | Observation used |
|---|---|
| [Docker Compose hash writer, main](https://raw.githubusercontent.com/docker/compose/main/cmd/compose/config.go) | Service name followed by hash; current main also resolves service environment before hashing. |
| [Docker Compose quiet images, main](https://raw.githubusercontent.com/docker/compose/main/cmd/compose/images.go) | Removes the ID algorithm prefix before quiet output. |
| [Docker Compose 5.4.0 hash writer](https://raw.githubusercontent.com/docker/compose/v5.4.0/cmd/compose/config.go) | Version-pinned runHash uses WithoutEnvironmentResolution and lacks current main's explicit environment-resolution step. Relevant to the separately observed env-file hash mismatch. |
| [Compose images reference](https://docs.docker.com/reference/cli/docker/compose/images/) | Images command and quiet/format options. |
| [PostgreSQL 16 information functions](https://www.postgresql.org/docs/16/functions-info.html) | Constraint definitions are reconstructed commands, not a general semantic-equivalence oracle or complete schema fingerprint. |
| [pnpm 10 run](https://pnpm.io/10.x/cli/run) | Script shorthand works only when its name does not collide with an existing pnpm command. |
| [pnpm 10 deploy](https://pnpm.io/10.x/cli/deploy) | Deploy is a native package-deployment command. |

The adjacent archive-bound recovery plan cites additional primary PostgreSQL material; its implementation and acceptance remain owned by the original task.
