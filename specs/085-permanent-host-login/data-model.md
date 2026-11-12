# Login policy and state

- Ordinary: existing future expires_at, one_time=true. No schema/output changes.
- Permanent: explicitly selected, expires_at=0 and one_time=false. Installed helper holds only a hash, configured user and canonical origin.
- One installed credential per target. Replacement changes its hash and invalidates old links. Existing sessions follow WordPress policy.
- No new database table or per-use state. Invalid requests never create an auth session.
