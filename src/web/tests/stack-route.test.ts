import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveStack } from "../src/ui/stack-route.ts";

const own = { slug: "guided-setup", source: "this-server" as const, remote: "xspeed-dev" };
const theirs = { slug: "guided-setup", source: "controller" as const, remote: "other-box" };

test("the canonical this-server segment finds this server's stack", () => {
  assert.deepEqual(resolveStack([own], "this-server", "guided-setup"), { stack: own, remote: "this-server" });
});

test("the row's remote name finds the same stack and asks for the canonical URL", () => {
  assert.deepEqual(resolveStack([own, theirs], "xspeed-dev", "guided-setup"), { stack: own, remote: "this-server" });
});

test("a controller record keeps its remote segment and is never redirected", () => {
  assert.deepEqual(resolveStack([own, theirs], "other-box", "guided-setup"), { stack: theirs, remote: "other-box" });
  const sameName = { ...theirs, remote: "xspeed-dev" };
  assert.deepEqual(resolveStack([own, sameName], "xspeed-dev", "guided-setup"), { stack: sameName, remote: "xspeed-dev" });
});

test("a name that points to more than one stack, or none, resolves to nothing", () => {
  assert.equal(resolveStack([own, { ...own }], "xspeed-dev", "guided-setup"), undefined);
  assert.equal(resolveStack([own], "unknown", "guided-setup"), undefined);
  assert.equal(resolveStack([own], "xspeed-dev", "other"), undefined);
  assert.equal(resolveStack([theirs], "this-server", "guided-setup"), undefined);
});
