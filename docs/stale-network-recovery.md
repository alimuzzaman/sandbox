# Stale Docker network recovery

When a managed local instance has an old container that refers to a Docker
network which no longer exists, `./sb up` retries once with
`docker compose up --force-recreate`. Compose recreates the network and that
instance's containers; volumes and other instances are not touched. If the
retry also fails, `up` returns the stable error code `stale_container_network`
(`mutated: true`, since containers were recreated).

Then recover only the named instance after checking that no operation is using it:

```sh
./sb down --instance NAME && ./sb up --instance NAME
```

The normal `down` path removes that instance's managed containers and network;
it does not request volume removal. If the instance is shared or its state is
unclear, stop and inspect it before retrying rather than using a broad Docker
cleanup command.

## Exhausted Docker address pools

Each instance gets its own Compose network. With many instances Docker can run
out of address pools, and `up` fails with "all predefined address pools have
been fully subnetted". Sandbox then removes `sandbox-*` networks that no
container references, running or stopped, and retries once; Compose recreates
a removed network on that instance's next `up`. If nothing can be removed, `up`
returns `docker_address_pools_exhausted`. Stop instances you do not need with
`./sb down --instance NAME`, which keeps their data.
