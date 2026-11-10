import { test } from "node:test";
import assert from "node:assert/strict";
import {
  applyLabel, timeLeft, lifetimeLabel, reapLabel, expiryTone, showBulkActions, hostedCountText,
  humanReason, CPU_LABEL, CPU_TITLE, type LifetimeLike,
} from "../src/ui/hostlabels.ts";

const NOW = 1_800_000_000;

function lifetime(extra: Partial<LifetimeLike> = {}): LifetimeLike {
  return {
    classification: "recorded", reason: null, lifetime: "temporary", owner: "Alice Example",
    expires_at: NOW + 7200, canonical: false, reap_eligible: false, reap_reason: null,
    state: "active", extended_count: 0, ...extra,
  };
}

test("an apply that ran but could not be verified is not called failed", () => {
  assert.deepEqual(applyLabel("unverified", "required_evidence_missing", true),
    { text: "applied · delivery unverified (required evidence missing)", tone: "warn" });
  assert.deepEqual(applyLabel("unverified", null, true), { text: "applied · delivery unverified", tone: "warn" });
  assert.deepEqual(applyLabel("failed", "binding_mismatch", true), { text: "failed (binding mismatch)", tone: "bad" });
  assert.deepEqual(applyLabel("failed", null, true), { text: "failed", tone: "bad" });
  assert.deepEqual(applyLabel("succeeded", null, true), { text: "succeeded", tone: "good" });
  assert.deepEqual(applyLabel("applying", null, true), { text: "applying", tone: "warn" });
});

test("targets applied before tracking say so instead of 'not recorded'", () => {
  assert.deepEqual(applyLabel(null, null, false), { text: "applied before tracking", tone: "quiet" });
  assert.deepEqual(applyLabel(undefined, undefined, false), { text: "applied before tracking", tone: "quiet" });
  assert.deepEqual(applyLabel(null, null, true), { text: "unknown", tone: "quiet" });
});

test("reasons are shown as words and odd input is dropped", () => {
  assert.equal(humanReason("required_evidence_missing"), "required evidence missing");
  assert.equal(humanReason("<b>x</b>"), "");
  assert.equal(humanReason(null), "");
});

test("time left reads naturally both sides of expiry", () => {
  assert.equal(timeLeft(NOW + 2 * 3600 + 5 * 60, NOW), "2 h 5 min left");
  assert.equal(timeLeft(NOW + 3 * 86400 + 3600, NOW), "3 d 1 h left");
  assert.equal(timeLeft(NOW + 30, NOW), "under a minute left");
  assert.equal(timeLeft(NOW - 3600, NOW), "expired 1 h ago");
  assert.equal(timeLeft(null, NOW), "");
});

test("expiry tone flags expired and near-expiry copies", () => {
  assert.equal(expiryTone(lifetime({ expires_at: NOW - 1 }), NOW), "bad");
  assert.equal(expiryTone(lifetime({ reap_eligible: true, reap_reason: "merged", expires_at: NOW + 99999 }), NOW), "bad");
  assert.equal(expiryTone(lifetime({ expires_at: NOW + 3600 }), NOW), "warn");
  assert.equal(expiryTone(lifetime({ expires_at: NOW + 3 * 86400 }), NOW), "quiet");
  assert.equal(expiryTone(lifetime({ lifetime: "hosted", expires_at: null }), NOW), "quiet");
});

test("lifetime labels: unclassified is protected, unknown is not guessed", () => {
  assert.equal(lifetimeLabel(lifetime()), "temporary");
  assert.equal(lifetimeLabel(lifetime({ lifetime: "hosted", canonical: true })), "hosted · canonical");
  assert.equal(lifetimeLabel(lifetime({ classification: "unclassified", reason: "invalid", lifetime: null })),
    "unclassified, protected");
  assert.equal(lifetimeLabel(lifetime({ classification: "unclassified", reason: "no_record", lifetime: null })),
    "unclassified, protected");
  assert.equal(lifetimeLabel(lifetime({ classification: "unknown", reason: "loading", lifetime: null })), "lifetime loading…");
  assert.equal(lifetimeLabel(lifetime({ classification: "unknown", reason: "unavailable", lifetime: null })), "lifetime unknown");
  assert.equal(lifetimeLabel(undefined), "lifetime unknown");
});

test("reap labels never call an unclassified or unknown row reapable", () => {
  assert.equal(reapLabel(lifetime({ reap_eligible: true, reap_reason: "expired" })), "reapable · expired");
  assert.equal(reapLabel(lifetime()), "not reapable");
  assert.equal(reapLabel(lifetime({ classification: "unclassified", reason: "invalid", lifetime: null, reap_eligible: true })),
    "protected");
  assert.equal(reapLabel(lifetime({ classification: "unknown", reason: "loading", lifetime: null, reap_eligible: true })),
    "unknown");
  assert.equal(reapLabel(undefined), "unknown");
});

test("bulk instance actions only with dev instances; hosted count text", () => {
  assert.equal(showBulkActions(0), false);
  assert.equal(showBulkActions(2), true);
  assert.equal(hostedCountText(7, 6), "7 hosted apps · 6 running");
  assert.equal(hostedCountText(1, 0), "1 hosted app · 0 running");
  assert.equal(hostedCountText(0, 0), "");
  assert.equal(hostedCountText(null, null), "");
});

test("CPU is labelled a point-in-time sample", () => {
  assert.match(CPU_LABEL, /sample/i);
  assert.match(CPU_TITLE, /point-in-time/i);
});
