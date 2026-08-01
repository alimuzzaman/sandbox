# Contract: Managed-Native Package Transaction

## Preview

`./sb native install-plan --project-dir DIR --json` is read-only and returns:

- advertised matrix ID and observed host compatibility;
- configured signed APT source identities;
- installed/candidate exact versions for host prerequisites;
- exact image package closure for PHP/MariaDB/nginx-or-Apache;
- install/upgrade/remove/hold actions from simulation;
- image-local maintainer script/service effects and suppression policy;
- host/image owned roots, image size/inodes, privilege/helper actions;
- unsupported/unavailable versions and Compose fallback;
- canonical `simulation_digest`.

No plan may add a repository, fetch/execute a remote installer, compile third-party source,
or substitute a requested version.

## Apply

`./sb native install --project-dir DIR` requires stdin/stdout attached to a current TTY and
displays the plan before confirmation. Confirmation is bound to the simulation digest and
expires on source/package/policy drift. MCP/CI returns `pending_install_confirmation` and
performs no mutation.

The installed root-owned helper accepts only fixed verbs and validated IDs/digests. It:

1. re-runs package simulation and rejects drift;
2. installs only missing approved host prerequisites;
3. creates the fixed-size image/rootfs;
4. uses configured Noble sources plus image-local `policy-rc.d` to install exact payload
   packages without starting a host service;
5. writes only owned root policy/nspawn files;
6. verifies host nginx/Apache/PHP-FPM/MariaDB enable/active/config/data state is unchanged;
7. records exact package/image/policy result.

Shared host prerequisites remain installed after final instance destroy. Removing unused
packages is a separate preview/confirmation operation.

## Result

Every result reports plan/apply digest, exact versions, changed package/path identities,
known service effects, verification, and `mutated`; it never includes repository
credentials, database passwords, or unrestricted package output.

