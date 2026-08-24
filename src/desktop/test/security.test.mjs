import assert from "node:assert/strict";
import test from "node:test";
import { isSameDashboardOrigin, parseDashboardUrl, parseExternalUrl } from "../dist/security.js";

test("dashboard endpoint is restricted to loopback HTTP", () => {
  assert.equal(parseDashboardUrl("http://127.0.0.1:8765").port, "8765");
  assert.equal(parseDashboardUrl("http://localhost:5199").hostname, "localhost");
  assert.throws(() => parseDashboardUrl("https://example.com"), /loopback/);
  assert.throws(() => parseDashboardUrl("file:///tmp/dashboard.html"), /loopback/);
  assert.throws(() => parseDashboardUrl("http://user:pass@localhost:8765"), /credentials/);
  assert.throws(() => parseDashboardUrl("http://localhost:8765/untrusted"), /origin/);
  assert.throws(() => parseDashboardUrl("http://localhost:8765/?token=nope"), /origin/);
});

test("external URLs accept only credential-free HTTP(S)", () => {
  assert.equal(parseExternalUrl("https://example.com/docs")?.hostname, "example.com");
  assert.equal(parseExternalUrl("mailto:test@example.com"), null);
  assert.equal(parseExternalUrl("file:///etc/passwd"), null);
  assert.equal(parseExternalUrl("https://user:pass@example.com"), null);
});

test("navigation remains on the configured dashboard origin", () => {
  const dashboard = parseDashboardUrl("http://127.0.0.1:8765");
  assert.equal(isSameDashboardOrigin("http://127.0.0.1:8765/instance/demo", dashboard), true);
  assert.equal(isSameDashboardOrigin("http://localhost:8765/", dashboard), false);
  assert.equal(isSameDashboardOrigin("https://example.com/", dashboard), false);
});
