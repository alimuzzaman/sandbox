import { test } from "node:test";
import assert from "node:assert/strict";
import { hostingPath, hostingTargetPath, parse } from "../src/router.ts";

test("hosting list and dedicated detail have distinct direct routes", () => {
  assert.deepEqual(parse(hostingPath()), { page: "hosting" });
  const path = hostingTargetPath("this-server", "guided-1111", "dev");
  assert.equal(path, "/hosting/this-server/guided-1111/dev");
  assert.equal(path.includes("#"), false);
  assert.deepEqual(parse(path), {
    page: "hosting", target: { remote: "this-server", project: "guided-1111", environment: "dev" },
  });
});

test("hosting detail preserves encoded names as individual segments", () => {
  const target = { remote: "server one", project: "site/a#b%", environment: "preview ü" };
  assert.deepEqual(parse(hostingTargetPath(target.remote, target.project, target.environment)), {
    page: "hosting", target,
  });
});

test("incomplete and malformed hosting detail URLs do not select another stack", () => {
  for (const path of ["/hosting/this-server", "/hosting/this-server/demo", "/hosting/this-server/demo/dev/extra", "/hosting/this-server/%ZZ/dev"]) {
    assert.deepEqual(parse(path), { page: "notfound" });
  }
});
