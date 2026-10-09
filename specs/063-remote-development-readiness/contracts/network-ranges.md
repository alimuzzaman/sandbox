# Contract: Development network ranges

## CLI

```
./sb remote network-range propose <remote> [--json]
./sb remote network-range assign  <remote> --cidr CIDR [--subnet-prefix N] [--confirm] [--json]
./sb remote network-range list    <remote> [--json]
./sb remote docker-pool <remote> [--confirm --plan-digest D] [--json]   # amended
```

- `propose`: read-only inventory, returns `{proposed: CIDR, subnet_prefix,
  assign_command}`, or `unknown` with a partial inventory. It never assigns.
- `assign` without `--confirm` returns `status: planned` with the overlap
  check result. With `--confirm` it records the range. Refusals:
  - `range_overlap` (data `class`: `docker_network` | `host_route` |
    `docker_default_pool` | `cgnat`);
  - `range_inventory_unknown`;
  - `range_runtime_unsupported` (remedy:
    `./sb remote service migrate <remote> --confirm --json`);
  - `range_conflict`.
- `list`: ranges, allocations (with subnets) and last-proven capacity; at most
  256 rows and 64 KiB; no secret-shaped values.

## Remote program (`sandbox/remote_network/program.py`)

The program reads one JSON request on stdin and writes one JSON line on
stdout. All writes happen under an exclusive flock on `state.lock`; `list`
and `inventory` take a shared lock or none.

| op | input | output |
|---|---|---|
| `inventory` | — | `{status: complete\|partial, networks:[cidr], routes:[cidr], range_support: true}` |
| `assign` | `{cidr, subnet_prefix, holder}` | `{assigned: bool, range_id}` or error code |
| `allocate` | `{owner_kind, owner_id, workspace_id, networks:[name]}` | `{granted:[{allocation_id, network, subnet}]}` or `{exhausted:true, table:[...]}`; all-or-nothing |
| `release-owner` | `{owner_id}` or `{workspace_id}` | `{released: n}` |
| `list` | — | `{ranges, allocations}` |

## Admission payload (protocol 2)

The capacity evidence gains a `range` object:
`{capacity, allocated, usable, granted:[allocation_id]}`. The evaluator's
public envelope adds `range_usable_subnets` and never echoes subnets.
Exhaustion returns `docker_network_subnet_exhausted` with `allocation_table`
rows `{owner_id, owner_kind, workspace_id, age_seconds}` (at most 32) and
`release_commands`.
`missing_pool_evidence` message: "no Docker address-pool evidence and no
Sandbox development range (missing evidence, not exhausted capacity); assign a
range: ./sb remote network-range propose <remote>".

## Compose override

`<workspace>/.sandbox/network-override.yml`, generated per run:

```yaml
networks:
  <name>:
    ipam:
      config:
        - subnet: <granted subnet>
```

It covers only the networks that the effective config creates. External
networks are listed in the run's evidence as `outside_range`.
