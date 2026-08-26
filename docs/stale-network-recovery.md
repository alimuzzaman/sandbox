# Stale Docker network recovery

When a managed local instance has an old container that refers to a Docker
network which no longer exists, `./sb up` returns the stable error code
`stale_container_network`. The command does not remove containers, volumes, or
other instances automatically.

Recover only the named instance after checking that no operation is using it:

```sh
./sb down --instance NAME && ./sb up --instance NAME
```

The normal `down` path removes that instance's managed containers and network;
it does not request volume removal. If the instance is shared or its state is
unclear, stop and inspect it before retrying rather than using a broad Docker
cleanup command.
