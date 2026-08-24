import assert from "node:assert/strict";
import { createServer, request } from "node:http";
import test from "node:test";
import { createAuthenticatedProxy, handshakeBackend } from "../dist/backend-proxy.js";

async function listen(handler) {
  const server = createServer(handler);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  return { server, origin: new URL(`http://127.0.0.1:${address.port}/`) };
}

function fetchRaw(url, cookie) {
  return new Promise((resolve, reject) => {
    const req = request(url, { headers: cookie ? { Cookie: cookie } : {} }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => resolve({ status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks).toString() }));
    });
    req.on("error", reject);
    req.end();
  });
}

test("backend handshake validates the bounded protocol shape", async (t) => {
  const backend = await listen((_request, response) => {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ instances: [{ name: "demo" }], remotes: [] }));
  });
  t.after(() => backend.server.close());
  assert.deepEqual(await handshakeBackend(backend.origin), { protocol: 1, instances: 1, remotes: 0 });
});

test("authenticated proxy rejects anonymous clients and hashes inline CSP", async (t) => {
  const backend = await listen((_request, response) => {
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end("<!doctype html><style>body{color:red}</style><button onclick=\"ready()\" style=\"color:red\">Ready</button><script>globalThis.ready=true</script>");
  });
  t.after(() => backend.server.close());
  const proxy = await createAuthenticatedProxy(backend.origin);
  t.after(() => proxy.close());
  assert.equal((await fetchRaw(proxy.origin)).status, 401);
  const response = await fetchRaw(proxy.origin, `${proxy.cookieName}=${proxy.cookieValue}`);
  assert.equal(response.status, 200);
  assert.match(response.headers["content-security-policy"], /sha256-/);
  assert.match(response.headers["content-security-policy"], /script-src-attr 'unsafe-hashes' 'sha256-/);
  assert.doesNotMatch(response.headers["content-security-policy"], /script-src(?:-elem)?[^;]*unsafe-inline/);
});
