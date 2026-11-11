import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { runInNewContext } from "node:vm";

// Evaluate the actual panel functions with only UI dependencies stubbed. No browser,
// fetch, storage, or generated bundle is involved in these pure rendering checks.
const source = readFileSync(new URL("../src/pages/share.ts", import.meta.url), "utf8")
  .replace(/^import[\s\S]*?;$/gm, "").replace(/^export /gm, "");
const esc = (s: string): string => String(s).replace(/[&<>"']/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c] || c));
const panel = runInNewContext(stripTypeScriptTypes(source, { mode: "transform" }) +
  "\n({shareCreationBlocked, linksTable, refusal})", {
    esc, theme: {}, refusalHelp: () => null, revocableId: () => true,
  });
const attempt = { attempt_id: "a-" + "a".repeat(32), link_id: "l-" + "b".repeat(12),
  phase: "cleanup_pending", expires_at: 1800000000 };
const inventory = { ok: true, attempts: [], links: [], sessions: [] };

test("uncertain inventory or unfinished attempts block creation", () => {
  assert.equal(panel.shareCreationBlocked(inventory), false);
  assert.equal(panel.shareCreationBlocked({ ...inventory, ok: false }), true);
  for (const phase of ["prepared", "minting", "revoking", "cleanup_pending"])
    assert.equal(panel.shareCreationBlocked({ ...inventory, attempts: [{ ...attempt, phase }] }), true);
  for (const phase of ["active", "revoked", "expired"])
    assert.equal(panel.shareCreationBlocked({ ...inventory, attempts: [{ ...attempt, phase }] }), false);
});

test("partial lists keep durable parent revoke and uncertainty visible", () => {
  const html = panel.linksTable({ ...inventory, ok: false, attempts: [attempt] });
  assert.match(html, /Live status is incomplete/);
  assert.match(html, /cleanup_pending/);
  assert.ok(html.includes(`data-revoke="${attempt.link_id}"`));
  assert.ok(!html.includes("No live links."));
});

test("coverage and remaining grant strings are escaped with exact ids and expiry", () => {
  const body = { innerHTML: "" };
  panel.refusal(body, { ok: false, code: "coverage", message: "<script>", gaps: [
    { stack: "<stack>", target: "<target>", hostname: "<host>", piece: "<piece>", code: "<code>", fix: "<fix>" },
  ] }, "r", "s");
  for (const value of ["script", "stack", "target", "host", "piece", "code", "fix"])
    assert.ok(body.innerHTML.includes(`&lt;${value}&gt;`));
  panel.refusal(body, { ok: false, code: "share_failed", message: "Cleanup pending", ...attempt,
    still_live: [{ id: attempt.link_id, target: "r/demo/dev", entry: "share", expires_at: 1800000000,
      status: "unknown", reason_code: "cleanup_unconfirmed" }],
  }, "r", "s");
  assert.ok(body.innerHTML.includes(attempt.link_id));
  assert.match(body.innerHTML, /r\/demo\/dev/);
  assert.match(body.innerHTML, /expires/);
  assert.match(body.innerHTML, /Repeat revoke/);
});
