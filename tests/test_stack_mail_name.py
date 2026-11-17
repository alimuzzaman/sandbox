"""A stack's tailnet-only inbox name follows the stack (stack.mail_hostname)."""
from __future__ import annotations

import dataclasses
import unittest
from pathlib import Path
from unittest import mock

from sandbox.hosting import stack_template
from sandbox.hosting.tailnet_names import models as m
from sandbox.hosting.tailnet_names import service, stack_mail
from tests.test_host_tailnet_name import SOON, ServiceTestCase, config

MAIL = "mail-demo.example.test"
OTHER = "mail-old.example.test"


class StackMailTests(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.slugs = {"demo": "live", "other": "live", "keep": "live", "old": "live"}

    def ensure(self, port=18188, slug="demo", fqdn=MAIL):
        return stack_mail.ensure("r1", slug, fqdn, port, context=self.context,
                                 liveness=lambda remote, entry: self.slugs, sleep=self.sleeps.append)

    def release(self, slug="demo"):
        return stack_mail.release("r1", slug, context=self.context, sleep=self.sleeps.append)

    def context(self, remote_name, *, public):
        self.assertEqual(remote_name, "r1")
        return config(), self.ctx, self.deps

    def record(self, fqdn=MAIL):
        return self.store.load()[fqdn]

    def test_apply_publishes_a_stack_tagged_name_and_a_rerun_changes_nothing(self):
        report = self.ensure()
        self.assertEqual(report, {"fqdn": MAIL, "status": "ready"})
        self.assertEqual((self.record().upstream, self.record().stack), ("127.0.0.1:18188", "demo"))
        issued = len(self.issued)
        self.assertEqual(self.ensure()["status"], "ready")
        self.assertEqual(len(self.issued), issued, "a rerun reuses the certificate")
        self.assertEqual(len(self.dns.records), 1)

    def test_a_changed_mailpit_port_replaces_the_upstream(self):
        self.ensure(port=18188)
        self.assertEqual(self.ensure(port=18190)["status"], "ready")
        self.assertEqual(self.record().upstream, "127.0.0.1:18190")
        self.assertIn("127.0.0.1:18190", self.remote.blocks[MAIL])
        self.assertNotIn("127.0.0.1:18188", self.remote.blocks[MAIL])

    def test_a_hand_made_name_is_never_re_pointed_or_taken(self):
        self.name, self.upstream = m.TailnetName(MAIL, "example.test"), m.Upstream("127.0.0.1", 9000)
        self.assertTrue(self.add().ok)
        report = self.ensure(port=18188)
        self.assertEqual((report["status"], report["code"]), ("failed", m.UPSTREAM_DIFFERS))
        self.assertEqual((self.record().upstream, self.record().stack), ("127.0.0.1:9000", None))
        # Same upstream: kept as it is, still untagged, so down never removes it.
        self.assertEqual(self.ensure(port=9000)["status"], "ready")
        self.assertIsNone(self.record().stack)
        self.assertEqual(self.release(), [])
        self.assertIn(MAIL, self.store.load())

    def test_a_block_published_elsewhere_with_another_upstream_is_refused(self):
        """Review M2: the controller's hand-made name has no record on the server."""
        self.name, self.upstream = m.TailnetName(MAIL, "example.test"), m.Upstream("127.0.0.1", 9000)
        self.assertTrue(self.add().ok)
        self.store.drop(MAIL)
        report = self.ensure(port=18188)
        self.assertEqual((report["status"], report["code"]), ("failed", m.UPSTREAM_DIFFERS))
        self.assertIn("127.0.0.1:9000", self.remote.blocks[MAIL])
        self.assertNotIn(MAIL, self.store.load())

    def test_a_name_of_another_live_stack_is_refused(self):
        self.ensure(slug="other", port=18200)
        report = self.ensure(slug="demo", port=18188)
        self.assertEqual((report["status"], report["code"]), ("failed", m.STACK_CONFLICT))
        self.assertEqual((self.record().stack, self.record().upstream), ("other", "127.0.0.1:18200"))

    def test_a_reaped_stack_name_on_a_reused_port_is_removed(self):
        self.assertEqual(self.ensure(slug="old", fqdn=OTHER, port=18188)["status"], "ready")
        report = self.ensure(slug="demo", port=18188)
        self.assertEqual((report["status"], report["swept"]), ("ready", [{"fqdn": OTHER, "status": "removed"}]))
        self.assertNotIn(OTHER, self.store.load())
        self.assertNotIn(OTHER, self.remote.blocks)
        self.assertEqual(sorted(self.dns.records), [MAIL])

    def test_orphans_are_swept_only_on_positive_evidence(self):
        """Review M3: a reap by the host timer, or a removal refused during down."""
        for state, swept in (("reaped", True), (None, True), ("live", False)):
            with self.subTest(state=state):
                self.ensure(slug="old", fqdn=OTHER, port=18300)
                self.slugs = {"demo": "live", **({"old": state} if state else {})}
                self.ensure(slug="demo")
                self.assertEqual(OTHER not in self.store.load(), swept)
                self.slugs["old"] = "live"
        # No lifetime listing: nothing is an orphan.
        report = stack_mail.ensure("r1", "demo", MAIL, 18188, context=self.context,
                                   liveness=lambda remote, entry: None)
        self.assertNotIn("swept", report)
        self.assertIn(OTHER, self.store.load())

    def test_down_removes_only_the_stack_names(self):
        self.ensure(slug="demo")
        self.ensure(slug="keep", fqdn=OTHER, port=18300)
        self.assertEqual(self.release(), [{"fqdn": MAIL, "status": "removed"}])
        self.assertEqual(sorted(self.store.load()), [OTHER])
        self.assertEqual(self.release(), [])

    def test_a_removal_rechecks_the_owner_and_upstream_under_the_lock(self):
        """Review m6: a stale snapshot never removes a name that changed since."""
        self.ensure(slug="demo", port=18190)
        for stack, upstream in (("other", None), ("demo", "127.0.0.1:18188")):
            with self.subTest(stack=stack):
                result = service.remove(self.ctx, self.deps, m.TailnetName(MAIL, "example.test"),
                                        expect_stack=stack, expect_upstream=upstream)
                self.assertEqual(result.code, m.STACK_CONFLICT)
                self.assertIn(MAIL, self.remote.blocks)

    def test_a_busy_host_is_retried_a_bounded_number_of_times(self):
        self.remote.locks = ["/run/lock/sandbox-docker-pool.lock"]
        report = self.ensure()
        self.assertEqual((report["status"], report["code"]), ("failed", m.BUSY))
        self.assertEqual(self.sleeps, [stack_mail.BUSY_WAIT_SECONDS] * (stack_mail.BUSY_ATTEMPTS - 1))
        self.remote.locks, self.sleeps[:] = [], []
        self.ensure()
        self.remote.locks = ["/run/lock/sandbox-docker-pool.lock"]
        self.assertEqual([row["code"] for row in self.release()], [m.BUSY])
        self.assertEqual(len(self.sleeps), stack_mail.BUSY_ATTEMPTS - 1, "down retries busy too")

    def test_a_host_without_tailnet_name_config_reports_and_never_raises(self):
        def missing(remote_name, *, public):
            raise m.TailnetNameError(m.CONFIG_MISSING, "add a tailnet_names block")
        report = stack_mail.ensure("r1", "demo", MAIL, 18188, context=missing)
        self.assertEqual((report["status"], report["code"]), ("unconfigured", m.CONFIG_MISSING))
        self.assertEqual(stack_mail.release("r1", "demo", context=missing), [])

        def broken(remote_name, *, public):
            raise RuntimeError("boom")
        self.assertEqual(stack_mail.ensure("r1", "demo", MAIL, 18188, context=broken)["status"], "failed")

    def due(self, fqdn, **changes):
        self.store.put(dataclasses.replace(self.record(fqdn), cert_not_after=SOON, **changes))
        self.remote.chains[fqdn]["not_after"] = SOON

    def test_an_apply_renews_one_due_live_published_stack_name(self):
        third = "mail-third.example.test"
        self.ensure(slug="old", fqdn=OTHER, port=18300)
        self.ensure(slug="keep", fqdn=third, port=18310)
        self.due(OTHER)
        self.due(third)
        report = self.ensure(slug="demo")
        self.assertEqual(len(report.get("renewed") or []), stack_mail.RENEWALS_PER_APPLY)
        renewed = report["renewed"][0]
        self.assertIn(renewed, (OTHER, third))
        left = third if renewed == OTHER else OTHER
        self.assertEqual(self.ensure(slug="demo").get("renewed"), [left], "the next apply takes the next")

    def test_orphans_and_unfinished_records_are_never_renewed(self):
        """Review M3/m8: a half-removed name is not brought back by a renewal."""
        self.ensure(slug="old", fqdn=OTHER, port=18300)
        self.due(OTHER, state="removing")
        self.assertNotIn("renewed", self.ensure(slug="demo"))
        self.assertEqual(self.record(OTHER).state, "removing")


class StackRecordTagTests(unittest.TestCase):
    def test_stack_tag_round_trips_and_is_validated(self):
        record = m.TailnetNameRecord(MAIL, "example.test", "127.0.0.1:18188", "100.64.0.10",
                                     stack="demo")
        self.assertEqual(m.TailnetNameRecord.parse(record.as_dict()).stack, "demo")
        untagged = dataclasses.replace(record, stack=None).as_dict()
        self.assertNotIn("stack", untagged)
        with self.assertRaises(m.TailnetNameError) as caught:
            m.TailnetNameRecord.parse({**record.as_dict(), "stack": "../x"})
        self.assertEqual(caught.exception.code, m.STATE_CORRUPT)


TEMPLATE = {
    "stack": {"slug": "^[a-z0-9-]+$", "ports": ["mailpit"], "mail_hostname": "mail-{slug}.example.test"},
    "project": "demo",
    "environments": {"dev": {"host": {"primary": "{slug}.example.test"}}},
}


class TemplateTests(unittest.TestCase):
    def parse(self, **stack):
        return stack_template.parse_template({**TEMPLATE, "stack": {**TEMPLATE["stack"], **stack}})

    def test_mail_hostname_needs_mailpit_and_one_slug(self):
        self.assertEqual(self.parse().mail_hostname, "mail-{slug}.example.test")
        for stack, why in (({"ports": []}, "mailpit"), ({"mail_hostname": "mail.example.test"}, "{slug}"),
                           ({"mail_hostname": "{slug}-{slug}.example.test"}, "{slug}"),
                           ({"mail_hostname": "Mail-{slug}.example.test"}, "{slug}"),
                           ({"mail_hostname": 7}, "{slug}")):
            with self.subTest(stack=stack), self.assertRaises(stack_template.StackTemplateError) as caught:
                self.parse(**stack)
            self.assertIn(why, str(caught.exception))

    def test_rendered_stack_carries_its_inbox_name(self):
        template = self.parse()
        ref = stack_template.StackRef(ref="main", commit="a" * 40, ref_branch="main")
        rendered = stack_template.render(template, slug="demo", ref=ref, ports={"mailpit": 18188},
                                         source_root="/src", template_path="/src/t.yml")
        block = rendered[stack_template.INSTANCE_KEY]
        self.assertEqual(block["mail_hostname"], MAIL)
        root = Path("/stacks")
        path = root / "demo" / stack_template.MANIFEST_NAME
        with mock.patch.object(Path, "resolve", lambda self: self):
            self.assertEqual(stack_template.rendered_instance(rendered, path, root)["mail_hostname"], MAIL)
            for bad in ("Mail-demo.example.test", "mail-{slug}.example.test", "x", 7):
                forged = {**rendered, stack_template.INSTANCE_KEY: {**block, "mail_hostname": bad}}
                with self.subTest(bad=bad), self.assertRaises(stack_template.StackTemplateError):
                    stack_template.rendered_instance(forged, path, root)
        plain = stack_template.parse_template({**TEMPLATE, "stack": {"slug": "^[a-z]+$", "ports": ["mailpit"]}})
        rendered = stack_template.render(plain, slug="demo", ref=ref, ports={"mailpit": 18188},
                                         source_root="/src", template_path="/src/t.yml")
        self.assertNotIn("mail_hostname", rendered[stack_template.INSTANCE_KEY])


class ServerHeldTests(unittest.TestCase):
    def test_server_held_selects_the_server_job_and_nothing_else_changes(self):
        from sandbox.hosting import server_cli_dispatch
        source = Path(server_cli_dispatch.__file__).read_text()
        self.assertIn('(action == "tailnet-name" and bool(getattr(args, "server_held", False)))', source)

    def test_server_held_is_refused_outside_a_server_job(self):
        from sandbox.commands import host_tailnet_name as cli
        args = mock.Mock(image_action="list", remote="r1", tailnet_hostname=None, upstream=None,
                         replace_upstream=False, force=False, all=False, json=True,
                         server_held=True, public_address=None, project_dir=None)
        target = mock.Mock(entry={"front_door": "nginx"})
        with mock.patch.object(cli, "resolve_context", return_value=mock.Mock(target=target)), \
                mock.patch.object(m.TailnetNameConfig, "load", side_effect=m.TailnetNameError(
                    m.CONFIG_MISSING, "x")), \
                mock.patch("builtins.print") as printed, self.assertRaises(SystemExit) as code:
            cli.cmd_host_tailnet_name(args)
        self.assertEqual(code.exception.code, 1)
        self.assertIn("--server-held needs a joined remote", printed.call_args.args[0])


class HookTests(unittest.TestCase):
    def test_a_completed_stack_apply_keeps_its_inbox_name(self):
        from sandbox.commands import hosting
        validated = {"stack": {"slug": "demo", "mail_hostname": MAIL, "ports": {"mailpit": 18188}}}
        with mock.patch.object(stack_mail, "ensure", return_value={"fqdn": MAIL, "status": "ready"}) as ensure:
            self.assertEqual(hosting._ensure_stack_mail_name(validated, "r1", {"delivery_state": "succeeded"}),
                             {"fqdn": MAIL, "status": "ready"})
            ensure.assert_called_once_with("r1", "demo", MAIL, 18188)
            for stack, state in ((validated["stack"], "failed"),
                                 ({"slug": "demo", "ports": {"mailpit": 18188}}, "succeeded"),
                                 ({"slug": "demo", "mail_hostname": MAIL, "ports": {}}, "succeeded")):
                self.assertIsNone(hosting._ensure_stack_mail_name({"stack": stack}, "r1",
                                                                  {"delivery_state": state}))
            self.assertEqual(ensure.call_count, 1)

    def test_reap_releases_the_names_of_reaped_stacks(self):
        from sandbox.commands import host_stack
        from sandbox.hosting.lifetime import reaper
        args = mock.Mock(plan=False, confirm=True, here=False, remote="r1")
        result = {"ok": True, "candidates": [{"compose_project": "p-a", "slug": "a"},
                                             {"compose_project": "p-b", "slug": "b"}],
                  "outcomes": [{"compose_project": "p-a", "status": "reaped"},
                               {"compose_project": "p-b", "status": "failed"}]}
        with mock.patch.object(host_stack, "_entry", return_value={}), \
                mock.patch.object(reaper, "run_remote", return_value=result), \
                mock.patch.object(stack_mail, "release",
                                  return_value=[{"fqdn": "mail-a.example.test", "status": "removed"}]) as release:
            payload = host_stack._reap(args)
        release.assert_called_once_with("r1", "a")
        self.assertEqual(payload["mail_names"], [{"fqdn": "mail-a.example.test", "status": "removed"}])


if __name__ == "__main__":
    unittest.main()
