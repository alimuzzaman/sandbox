# Ubuntu 24.04 coexistence with foreign host services (039 T047)

**Host**: Ubuntu 24.04.4 LTS. 2026-08-04.

**Status**: baseline coexistence proven; the package-install transaction itself is not
re-proven here (see `isolation-prerequisites.md`, which recorded it).

## Foreign state before and after

The proof host runs Caddy on ports 80 and 443 and has nginx, apache2, mysql and mariadb
installed but inactive — a host that already owns the endpoints a native runtime would
naively take.

```text
before   caddy LISTEN *:80, *:443
         nginx inactive   apache2 inactive   mysql inactive   mariadb inactive
after    identical
```

`foreign_host_service_baseline: true` in the acceptance run: the helper's
`host-baseline-observe` reading is unchanged across provisioning two managed instances and
destroying them. Neither instance enabled, started or rewrote a host service, and both took
their own veth address and backend port rather than a host endpoint.

`host_veth_sentinel_active: true`: a sentinel listener on the host veth stayed reachable
from the host throughout, and unreachable from inside either instance.

## Not covered

- A fresh interactive package transaction on a host missing the prerequisites; this host
  already had them from the earlier run recorded in `isolation-prerequisites.md`.
- Destroy leaving shared packages installed is asserted by contract but not re-measured
  here.
