import { test } from "node:test";
import assert from "node:assert/strict";
import { stackSignInAction } from "../src/ui/stack-signin.ts";

test("available stack access renders one safe one-time WordPress admin action", () => {
  const html = stackSignInAction({
    remote: "remote&one", slug: "demo<stack>",
    page_access: { sign_in: { available: true, code: "sign_in_available", message: "Sign in to WordPress admin" } },
  });

  assert.match(html, /WordPress admin/);
  assert.match(html, /fresh one-time login/i);
  assert.match(html, /data-remote="remote&amp;one"/);
  assert.match(html, /data-stack="demo&lt;stack&gt;"/);
  assert.match(html, /onclick="sb\.signInStack\(this\)"/);
  assert.doesNotMatch(html, /https?:|autologin|login_url/i);
});

test("unavailable stack access renders disabled guidance without a login URL", () => {
  const html = stackSignInAction({
    remote: "remote", slug: "demo",
    page_access: { sign_in: { available: false, code: "sign_in_not_declared", message: "Declare WordPress autologin and reapply." } },
  });

  assert.match(html, /<button[^>]*disabled/);
  assert.match(html, /WordPress admin/);
  assert.match(html, /Declare WordPress autologin and reapply\./);
  assert.doesNotMatch(html, /onclick=|https?:/i);
});

test("unknown stack access remains visibly disabled with refresh guidance", () => {
  const html = stackSignInAction({ remote: "remote", slug: "demo" });

  assert.match(html, /<button[^>]*disabled/);
  assert.match(html, /Refresh this stack page on its controller/);
});
