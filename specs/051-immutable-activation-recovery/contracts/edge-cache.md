# Edge-cache purge contract

## Policy

`cloudflare.cache_purge` is optional. When present, it is a closed mapping with
`on_deploy: true`, `scope: zone_all`, and one to 32 normalized DNS zone names.
Every non-wildcard declared route must end in one approved zone. The policy digest
is part of the deployment request and activation generation identity.

## Receipt

A receipt is schema-versioned and bounded. It contains the request identity and
digest, provider (`cloudflare`), scope (`zone_all`), normalized routes, the policy
digest, and an ordered zone list. Every zone entry contains only the zone ID/name,
state, and a bounded provider acknowledgement ID. No token, URL credential, or
raw provider response is persisted.

## Evidence levels

An acknowledged Cloudflare purge proves the API accepted a zone-wide purge request
for that zone. It does not prove global propagation or that a particular frontend
generation is already served everywhere. Fresh route/runtime observations remain
separate required evidence.

## Recovery and replay

The durable target owner writes the operation before effect entry. Exact terminal
replay is read-only. A possible POST with no exact acknowledgement is
`acceptance_unknown`, remains fenced, and is never automatically replayed by
observation recovery. A changed policy, route, zone, generation, or request digest
conflicts with the retained operation.
