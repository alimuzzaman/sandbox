import { test } from "node:test";
import assert from "node:assert/strict";
import { canShare, refusalHelp, revocableId, shareTerms } from "../src/ui/sharelabels.ts";

const NOW = 1_800_000_000;

test("the two owner-fixable refusals say what to change", () => {
  assert.match(refusalHelp("agent_mint_not_enabled", "x", "xspeed-dev", "demo") || "", /share\.agent_mint: true/);
  assert.match(refusalHelp("stack_share_not_declared", "x", "xspeed-dev", "demo") || "", /`share:` block.*sb host stack up/);
});

test("a panel without the controller record points at the controller command", () => {
  const help = refusalHelp("share_failed", "no remote named 'xspeed-dev'", "xspeed-dev", "demo") || "";
  assert.match(help, /sb host share create --remote xspeed-dev --stack demo --json/);
  assert.match(refusalHelp("stack_unknown", "no stack named", "xspeed-dev", "demo") || "", /controller/);
  assert.equal(refusalHelp("share_failed", "another host apply or recovery owns this target", "r", "s"), null);
  assert.equal(refusalHelp("share_ttl_invalid", "bad", "r", "s"), null);
});

test("terms show time left and the use limit", () => {
  assert.equal(shareTerms(NOW + 7 * 86400 - 3600, null, false, NOW), "expires in 6d 23h · no use limit");
  assert.equal(shareTerms(NOW + 5400, 1, false, NOW), "expires in 1h 30m · 1 use");
  assert.equal(shareTerms(NOW + 120, 3, true, NOW), "expires in 2m (shortened to the gate's end date) · 3 uses");
  assert.equal(shareTerms(NOW - 10, null, false, NOW), "expires in 0m · no use limit");
});

test("only a named remote gets the buttons, and only one id is revocable", () => {
  assert.equal(canShare("xspeed-dev"), true);
  assert.equal(canShare("this-server"), false);
  assert.equal(canShare("Bad Name"), false);
  assert.equal(revocableId("l-" + "a".repeat(32)), true);
  assert.equal(revocableId("s-" + "0".repeat(32)), true);
  assert.equal(revocableId("all"), false);
  assert.equal(revocableId("l-" + "A".repeat(32)), false);
});
