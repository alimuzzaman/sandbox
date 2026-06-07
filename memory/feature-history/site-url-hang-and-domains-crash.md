# Bug: production "loading forever" on a custom-domain instance + `domains list` crash

Reported: users on the production install (`curl … | sh` from sandbox.xc1.app)
saw an instance "load and load" forever when visiting it with a custom domain.
Reproduced by installing the prod tarball into an isolated dir and creating
instances. Two distinct bugs, same area (domain/URL handling).

## Bug 1 — the hang (`site_url` / `_site_url`)

`site_url()` precedence had a bad fallback:

```
if dom: return f"http://{dom}:{port}"     # <- WRONG
```

A `.sb` domain only resolves while the proxy + its `*.sb` DNS (lo0 alias +
dnsmasq) are up. When a domain is set but the proxy ISN'T serving it — proxy
down, DNS not installed (the prod/server case), or the lo0 alias dropped after a
reboot — that `http://<domain>:<port>` URL points at a host that won't resolve.
The browser just spins → "loading forever." The `:port` form is also never
valid even when DNS works: the proxy serves clean URLs with NO port.

Fix: drop the `http://<domain>:<port>` branch entirely. Fall back to
`http://localhost:<port>`, which always works (the WP container publishes that
port). Fixed in BOTH `sb` (`site_url`) and `mcp/wp-server/server.py`
(`_site_url`) — they mirror each other; fixing only one would desync the URL the
dashboard shows vs. what MCP reports.

## Bug 2 — `./sb domains list` crash

```
AttributeError: 'NoneType' object has no attribute 'endswith'
  at  ic.get("domain", "").endswith(f".{PROXY_TLD}")
```

`resolve_instances` sets `"domain": inst.get("domain")`, which is `None` when an
instance block has no domain key. `dict.get("domain", "")` only uses the default
when the key is ABSENT — here the resolved key is present with value `None`, so
`.get` returns `None` and `.endswith` blows up. Fix: `(ic.get("domain") or "")`.
This is the general footgun for any `.get("domain", "").<strmethod>` in the file
— the rest already use the safe `dom = ic.get("domain"); if dom and …` form.

## Testing gotcha (cost real time — DON'T redo)

Testing the prod install ON THE SAME MACHINE as the dev sandbox is dangerous:
both share the Docker daemon AND the `sandbox-main` compose project name, and
both register the user-scope `sandbox` MCP server. Running `./sb uninstall` (or
even `down`) from the throwaway prod install **swept the dev `main` instance**
(stopped its containers) and **deregistered the shared `sandbox` MCP server**.
Recovered with `./sb up --instance main` (containers were only stopped, not
wiped — different project prefixes saved xspeed/embedpress/betterdocs).

Next time: test a prod install in a VM / separate machine, or at minimum never
run `uninstall`/`clean`/`down` from it. Create throwaway instances with unique
names and delete only those.

## Verified

- `./sb domains list` on the prod install: was `AttributeError`, now exits 0.
- `site_url()` with `domain=None` → `http://localhost:<port>`; with a `.sb`
  domain while proxy not serving → `http://localhost:<port>` (was the broken
  `http://<domain>:<port>` that hangs).
