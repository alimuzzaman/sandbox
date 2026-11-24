import { test } from "node:test";
import assert from "node:assert/strict";
import { factRows } from "../src/ui/stackfacts.ts";

test("a url fact served by another stack links to that stack's page and its address", () => {
  const rows = factRows([{ label: "Upstream", kind: "url", value: "https://api.example.test", page_path: "/hosting/this-server/api-one" }]);
  assert.deepEqual(rows, [{ label: "Upstream", text: "https://api.example.test", href: "https://api.example.test",
    stackPath: "/hosting/this-server/api-one" }]);
});

test("a url fact no stack serves keeps only its address", () => {
  const [row] = factRows([{ label: "Upstream", kind: "url", value: "https://elsewhere.example.test/x", page_path: null }]);
  assert.equal(row.href, "https://elsewhere.example.test/x");
  assert.equal(row.stackPath, null);
});

test("text facts and non-https values never become links", () => {
  const rows = factRows([
    { label: "Region", kind: "text", value: "eu-west", page_path: "/hosting/x/y" },
    { label: "Bad", kind: "url", value: "javascript:alert(1)", page_path: "//evil.example" },
  ]);
  assert.deepEqual(rows.map(row => [row.href, row.stackPath]), [[null, null], [null, null]]);
});

test("a stack with no facts renders no rows", () => {
  assert.deepEqual(factRows(undefined), []);
});
