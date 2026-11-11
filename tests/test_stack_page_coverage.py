"""Pure coverage and private v2 wire contracts. No runtime or mint operations."""
import copy
import unittest
from unittest.mock import patch

from sandbox.hosting.share import ShareRefusal, short_link_id
from sandbox.hosting.share import stack_page as page


def context(**changes):
    values = dict(remote="test", slug="parent", record={"hostnames": ["app.example.test"]},
                  validated={"routes": [{"hostname": "app.example.test", "mode": "serve",
                                         "primary": True}], "compose": {"service": "web"}},
                  declaration={"allowed_host_suffixes": ["example.test"], "entries": {
                      "share": {"mode": "site_gate", "hostname": "share.example.test"}},
                      "share": {"hostname": "share.example.test", "stack_sign_in": [],
                                "route_sign_in": {}, "inbox_sign_in": None, "linked_access": {}}})
    values.update(changes)
    return page.TargetContext(**values)


class CoverageTests(unittest.TestCase):
    def test_unmapped_route_is_explicitly_open(self):
        result = page.coverage(context())
        self.assertEqual(result.gaps, ())
        self.assertEqual(result.hosts[0]["access"], "open")

    def test_missing_linked_access_and_basic_auth_both_reported(self):
        ctx = context()
        ctx.validated["basic_auth"] = {"enabled": True}
        ctx.record["sites"] = [{"hostname": "site.example.test"}]
        result = page.coverage(ctx)
        self.assertIn("basic_auth", {g.code for g in result.gaps})
        self.assertIn("linked_access_unknown", {g.code for g in result.gaps})

    def test_bare_open_link_without_declaration_refuses(self):
        ctx = context(commit="a" * 40, state="serving")
        ctx.record["sites"] = [{"hostname": "site.example.test", "access": "open"}]
        self.assertIn("linked_access_unknown", {g.code for g in page.coverage(ctx).gaps})
        with self.assertRaises(ShareRefusal):
            page.build_v2(ctx)

    def test_declared_recorded_link_uses_own_revision_and_state(self):
        ctx = context(commit="a" * 40, state="serving")
        ctx.declaration["share"]["linked_access"] = {"site.example.test": "open"}
        ctx.record["sites"] = [{"hostname": "site.example.test", "commit": "b" * 40,
                                "state": "failed"}]
        result = page.coverage(ctx)
        self.assertEqual(result.gaps, ())
        row = next(r for r in result.hosts if r["probe"] == "public")
        self.assertEqual((row["access"], row["commit"], row["state"]),
                         ("open", "b" * 40, "failed"))
        ctx.record["sites"][0].update(commit="invalid", state="verified")
        row = next(r for r in page.coverage(ctx).hosts if r["probe"] == "public")
        self.assertEqual((row["commit"], row["state"]), (None, "unknown"))
        ctx.record["sites"][0] = {"hostname": "site.example.test"}
        row = next(r for r in page.coverage(ctx).hosts if r["probe"] == "public")
        self.assertEqual((row["commit"], row["state"]), (None, "unknown"))

    def test_linked_context_rows_and_unused_declarations(self):
        ctx = context(linked_sites=({"hostname": "actual.example.test"},))
        ctx.record["sites"] = [{"hostname": "unused.example.test"}]
        ctx.declaration["share"]["linked_access"] = {"unused.example.test": "open"}
        gaps = page.coverage(ctx).gaps
        self.assertEqual(gaps, tuple(sorted(gaps)))
        self.assertIn(("actual.example.test", "linked_access_unknown"),
                      {(g.hostname, g.code) for g in gaps})
        self.assertIn(("unused.example.test", "linked_access_unrecorded"),
                      {(g.hostname, g.code) for g in gaps})

    def test_declared_link_still_refuses_known_restrictions(self):
        for restriction in ({"access": "protected"}, {"basic_auth": True},
                            {"private": True}, {"requires_auth": True}):
            ctx = context()
            ctx.declaration["share"]["linked_access"] = {"site.example.test": "open"}
            ctx.record["sites"] = [{"hostname": "site.example.test", **restriction}]
            with self.subTest(restriction=restriction):
                self.assertIn("linked_access_unsupported", {g.code for g in page.coverage(ctx).gaps})
                with self.assertRaises(ShareRefusal):
                    page.build_v2(ctx)

    def test_invalid_link_declarations_and_record_rows_keep_gaps(self):
        for value in (None, [], {"foreign.invalid": "open"},
                      {"site.example.test": "protected"},
                      {f"s{i}.example.test": "open" for i in range(17)}):
            ctx = context(); ctx.declaration["share"]["linked_access"] = value
            ctx.record["sites"] = [{"hostname": "site.example.test"}, {"hostname": "bad/host"}]
            with self.subTest(value=value):
                codes = {g.code for g in page.coverage(ctx).gaps}
                self.assertTrue({"linked_access_invalid", "linked_access_unknown", "host_invalid"} <= codes)
                with self.assertRaises(ShareRefusal):
                    page.prepare_pages(ctx)

    def test_declared_link_cannot_override_source_basic_auth(self):
        ctx = context()
        ctx.validated["basic_auth"] = {"enabled": True}
        ctx.record["sites"] = [{"hostname": "site.example.test"}]
        ctx.declaration["share"]["linked_access"] = {"site.example.test": "open"}
        self.assertIn("basic_auth", {g.code for g in page.coverage(ctx).gaps})
        with self.assertRaises(ShareRefusal):
            page.prepare_pages(ctx)

    def test_link_duplicate_of_own_host_never_inherits_parent_revision(self):
        ctx = context(commit="a" * 40, state="serving")
        ctx.record["sites"] = [{"hostname": "app.example.test"}]
        ctx.declaration["share"]["linked_access"] = {"app.example.test": "open"}
        row = page.coverage(ctx).hosts[0]
        self.assertEqual((row["probe"], row["commit"], row["state"]),
                         ("public", None, "unknown"))

    def test_declared_off_suffix_record_still_refuses(self):
        ctx = context()
        ctx.record["sites"] = [{"hostname": "foreign.invalid"}]
        ctx.declaration["share"]["linked_access"] = {"foreign.invalid": "open"}
        self.assertIn("host_suffix", {g.code for g in page.coverage(ctx).gaps})

    def test_public_linked_wordpress_adds_no_admin_signin(self):
        ctx = context()
        ctx.declaration["share"]["linked_access"] = {"wp.example.test": "open"}
        ctx.record["sites"] = [{"hostname": "wp.example.test", "autologin": {"user": "admin"}}]
        prepared = page.build_v2(ctx)
        self.assertIsNone(prepared["wordpress"])
        self.assertEqual(prepared["sign_in"], [])

    def test_aggregates_parent_and_direct_identity_in_stable_order(self):
        a = context(); a.record["ports"] = {"mailpit": 12000}
        b = context(slug="child", remote="elsewhere")
        b.record["hostnames"] = ["foreign.invalid"]
        gaps = page.coverage_all(a, [b])
        self.assertEqual(gaps, tuple(sorted(gaps)))
        self.assertIn("remote_mismatch", {g.code for g in gaps})
        self.assertIn("inbox_uncovered", {g.code for g in gaps})
        self.assertIn("host_suffix", {g.code for g in gaps})
        self.assertEqual({g.slug for g in gaps}, {"parent", "child"})

    def test_no_truncation_of_overflow_or_invalid_hosts(self):
        ctx = context()
        ctx.record["hostnames"] = [f"s{i}.example.test" for i in range(18)] + ["bad/host"]
        result = page.coverage(ctx)
        self.assertGreater(len(result.hosts), 16)
        self.assertIn("host_overflow", {g.code for g in result.gaps})
        self.assertIn("host_invalid", {g.code for g in result.gaps})
        with self.assertRaises(ShareRefusal):
            page.build_v2(ctx)

    def test_page_preflight_happens_without_key_generation(self):
        ctx = context(); ctx.record["sites"] = [{"hostname": "site.example.test"}]
        with patch.object(page.secrets, "token_hex", side_effect=AssertionError("key effect")):
            with self.assertRaises(ShareRefusal):
                page.build_v2(ctx)

    def test_v2_preparation_and_wire_are_exact_and_bounded(self):
        prepared = page.build_v2(context())
        record = page.gate_record(prepared, "share", "l-" + "a" * 12)
        self.assertEqual(record["schema"], page.STACK_SCHEMA_V2)
        self.assertTrue(page.valid_stack_v2(record))
        self.assertFalse(page.valid_stack_v2({**record, "extra": True}))
        self.assertEqual(set(record), {"schema", "entry", "link", "title", "hosts", "notes",
                                       "wordpress", "sign_in", "inbox", "companions"})
        record["notes"] = ["x" * page.MAX_STACK_PAGE_BYTES]
        self.assertFalse(page.valid_stack_v2(record))

    def test_companion_is_direct_and_token_matches_preallocated_id(self):
        child = {"slug": "child", "target": "test/project/dev", "hostname": "child.example.test",
                 "token": "A" * 22, "link_id": short_link_id("A" * 22), "expires_at": 2000000000,
                 "title": "child", "hosts": [], "notes": []}
        record = page.gate_record(page.build_v2(context(), companions=[child]),
                                  "share", "l-" + "b" * 12)
        self.assertTrue(page.valid_stack_v2(record))
        record["companions"][0]["companions"] = []
        self.assertFalse(page.valid_stack_v2(record))

    def test_claim_renderer_delegates_only_when_origin_present(self):
        with patch("sandbox.hosting.share.wordpress.render_claim_plugin", create=True,
                   return_value="php") as renderer:
            self.assertEqual(page.render_key_plugin("a" * 12, "b" * 64, "admin", 2000000000,
                                                    claim_origin="https://share.example.test"), "php")
            renderer.assert_called_once_with("a" * 12, "b" * 64, "admin", 2000000000,
                                             "https://share.example.test", now=None)


class TemplateCoverageTests(unittest.TestCase):
    def test_only_route_mapping_keys_gain_placeholders(self):
        from sandbox.hosting import stack_template as template
        path = ("environments", "dev", "share", "route_sign_in")
        template._scan({"app-{slug}.example.test": "app"}, path, ())
        first = template._render_node({"app-{slug}.example.test": "app"}, path, {"slug": "one"})
        second = template._render_node({"app-{slug}.example.test": "app"}, path, {"slug": "two"})
        self.assertEqual(first, {"app-one.example.test": "app"})
        self.assertEqual(second, {"app-two.example.test": "app"})
        with self.assertRaises(template.StackTemplateError):
            template._scan({"app.example.test": "{slug}"}, path, ())
        with self.assertRaises(template.StackTemplateError):
            template._scan("{slug}", ("environments", "dev", "share", "inbox_sign_in"), ())

    def test_linked_hostname_placeholders_are_approved_and_collisions_refuse(self):
        from sandbox.hosting import stack_template as template
        path = ("environments", "dev", "share", "linked_access")
        template._scan({"wp-{slug}.example.test": "open"}, path, ())
        self.assertEqual(template._render_node({"wp-{slug}.example.test": "open"}, path,
                                              {"slug": "one"}), {"wp-one.example.test": "open"})
        with self.assertRaises(template.StackTemplateError):
            template._scan({"wp.example.test": "{slug}"}, path, ())
        with self.assertRaises(template.StackTemplateError):
            template._scan({"wp-{unknown}.example.test": "open"}, path, ())
        with self.assertRaises(template.StackTemplateError):
            template._render_node({"wp-{slug}.example.test": "open", "wp-one.example.test": "open"},
                                  path, {"slug": "one"})

    def test_all_page_preflight_aggregates_email_failures(self):
        a = context(); b = context(slug="child")
        for ctx in (a, b):
            ctx.declaration["share"]["stack_sign_in"] = ["app"]
            ctx.declaration["entries"]["app"] = {"mode": "magic_link", "magic_link": {"denied_emails": []}}
        with self.assertRaises(ShareRefusal) as caught:
            page.prepare_pages(a, [b])
        self.assertIn("test/parent", caught.exception.message)
        self.assertIn("test/child", caught.exception.message)
