"""Owner-panel gate: one full target snapshot at bind, cheap bound re-checks after.

Synthetic owner, records and Docker replies only; nothing reaches a daemon.
"""
from __future__ import annotations

import contextlib
import json
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.hosting import owner_panel_gate as gate
from sandbox.hosting import owner_panel_share as share
from sandbox.hosting.owner_share_record import digest
from sandbox.hosting.share import ShareRefusal

PROJECT, ENV, SLUG = "example-site", "production", "example"
COMPOSE = f"sandbox-host-{PROJECT}-{ENV}"
GATE_ID, WP_ID, DB_ID = "a" * 64, "b" * 64, "c" * 64


class _Docker:
    """Answers `docker inspect` of bound ids and the gate capabilities query."""

    def __init__(self, declaration, access):
        self.command = ["python3", "-c", gate.login._LOADER, gate.login._runtime_source()]
        self.containers = {
            GATE_ID[:12]: [GATE_ID, True, COMPOSE, "sandbox-login", "1", self.command],
            WP_ID[:12]: [WP_ID, True, COMPOSE, "wordpress", "1", ["apache2-foreground"]],
            DB_ID[:12]: [DB_ID, True, COMPOSE, "db", "1", ["mysqld"]],
        }
        self.reply = {"ok": True, "config_digest": access["gate_config_digest"],
                      "panel_access_digest": digest(declaration),
                      "panel_target_digest": access["target_digest"],
                      "capabilities": ["owner-panel-gate-v1"]}
        self.inspects = 0
        self.queries = 0
        self.effects = []

    def run(self, argv, **_):
        if argv[:2] == ["docker", "inspect"]:
            self.inspects += 1
            ids = argv[4:]
            if any(ident not in self.containers for ident in ids):
                return subprocess.CompletedProcess(argv, 1, "", "Error: No such object: x\n")
            return subprocess.CompletedProcess(
                argv, 0, "".join(json.dumps(self.containers[i]) + "\n" for i in ids), "")
        if argv[:2] == ["docker", "exec"]:
            self.queries += 1
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.reply), "")
        raise AssertionError(f"unexpected command {argv[:2]}")

    def popen(self, argv, **_):
        self.effects.append(argv[:5])
        child = SimpleNamespace(pid=4242, returncode=0)
        child.communicate = lambda data=b"", timeout=None: (b"", b"")
        return child


class GateRecheckTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.owner = SimpleNamespace(sandbox_home=directory.name, home=directory.name, uid=1000)
        declaration = {"share": {"agent_mint": True, "hostname": "share.example.test"},
                       "entries": {"share": {"mode": "site_gate"}}}
        access = {"copy_id": "0123abcd", "gate_config_digest": "g" * 64, "target_digest": "t" * 64,
                  "validated": {"project": PROJECT, "environment": ENV, "login_service": declaration,
                                "compose": {"service": "web"},
                                "autologin": {"service": "wordpress", "user": "admin", "ttl_seconds": 900,
                                              "container_path": "/var/www/html/wp-content/mu-plugins/x.php"}}}
        self.summary = {"project": PROJECT, "environment": ENV, "commit": "d" * 40,
                        "last_apply": {"status": "succeeded"}, "stack": {"slug": SLUG},
                        "owner_share": access, "remote": "fixture"}
        self.snapshot = {"ok": True, "project": PROJECT, "environment": ENV, "name": SLUG,
                         "copy_id": "0123abcd", "source_commit": "d" * 40, "source_origin_commit": "e" * 40,
                         "native_keys": [f"fixture/{PROJECT}/{ENV}"],
                         "services": [{"name": "db", "index": 1, "container_id": DB_ID[:12]},
                                      {"name": "sandbox-login", "index": 1, "container_id": GATE_ID[:12]},
                                      {"name": "wordpress", "index": 1, "container_id": WP_ID[:12]}]}
        self.lifetime = {"compose_project": COMPOSE, "state": "active", "reap_run": None,
                         "copy_id": "0123abcd", "commit": "e" * 40, "expires_at": 1}
        self.docker = _Docker(declaration, access)
        self.full = 0
        self.current_owner = self.owner

        def inspect_target(**_):
            self.full += 1
            return json.loads(json.dumps(self.snapshot))

        scope = contextlib.ExitStack()
        self.addCleanup(scope.close)
        scope.enter_context(patch("sandbox.application.server_cli_targets.inspect_current_server_target",
                                  side_effect=inspect_target))
        scope.enter_context(patch.object(gate, "current_owner", side_effect=lambda: self.current_owner))
        scope.enter_context(patch.object(gate, "_environment", return_value={}))
        scope.enter_context(patch.object(gate.subprocess, "run", side_effect=self.docker.run))
        scope.enter_context(patch.object(gate.subprocess, "Popen", side_effect=self.docker.popen))
        scope.enter_context(patch.object(gate.login, "assert_applicable"))
        scope.enter_context(patch.object(gate.login, "parse_site_reply", side_effect=json.loads))
        scope.enter_context(patch.object(gate.panel_records, "read_server_records",
                                         side_effect=lambda _: {"targets": [json.loads(json.dumps(self.summary))]}))
        scope.enter_context(patch.object(gate.panel_records, "_read_json",
                                         side_effect=lambda _: json.loads(json.dumps(self.summary))))
        scope.enter_context(patch.object(gate.panel_records, "parse_target_summary", side_effect=lambda value: value))
        scope.enter_context(patch("sandbox.hosting.lifetime.hoststore.Store.peek",
                                  side_effect=lambda *_: {"records": [dict(self.lifetime)], "invalid": [],
                                                          "truncated": False}))
        self.request = object.__new__(gate.PanelRequest)
        for key, value in zip(("action", "remote", "stack", "link_id", "_seal"),
                              ("create", "fixture", SLUG, None, gate._SEAL)):
            object.__setattr__(self.request, key, value)

    def bind(self):
        return gate.bind(self.request)

    def test_bind_takes_one_full_snapshot_and_rechecks_are_one_inspect(self):
        grant = self.bind()
        self.assertEqual(self.full, 1)
        self.assertEqual((grant.gate_id, grant.wordpress_id), (GATE_ID, WP_ID))
        before = self.docker.inspects
        grant.require_current()
        grant.require_current()
        self.assertEqual(self.full, 1)
        self.assertEqual(self.docker.inspects - before, 2)

    def test_wordpress_admin_click_takes_one_full_snapshot(self):
        store = SimpleNamespace(root=self.owner.home, rows=lambda *_: [], save=lambda row: None)

        with patch.object(share, "Store", return_value=contextlib.nullcontext(store)), \
                patch.object(share, "_held", return_value=contextlib.nullcontext()), \
                patch.object(share, "_audit"), \
                patch.object(share.login, "is_private", return_value=False), \
                patch.object(share.login, "enabled_until_epoch", return_value=4_000_000_000), \
                patch("sandbox.core._hosting.autologin_url", return_value="redacted"):
            result = share.wordpress_admin(self.request)
        self.assertTrue(result["ok"])
        self.assertEqual(self.full, 1)
        self.assertEqual(self.docker.effects, [["docker", "exec", "-i", WP_ID, "php"]])
        # The admin effect runs only in WordPress; the gate config query is skipped.
        self.assertEqual(self.docker.queries, 0)
        # The binding at bind, then the one re-check right before the effect.
        self.assertEqual(self.docker.inspects, 2)

    def test_share_bind_queries_the_running_gate_once(self):
        self.bind()
        self.assertEqual(self.docker.queries, 1)

    def test_a_wordpress_only_grant_cannot_run_gate_verbs(self):
        grant = gate.bind(self.request, gate_verbs=False)
        store = SimpleNamespace(save=lambda value: None)
        for verb in ("mint", "list", "revoke"):
            with self.subTest(verb=verb), self.assertRaises(ShareRefusal):
                gate._execute(grant, verb, {}, store, payload={}, grant_id="l-" + "0" * 32)
        self.assertEqual(self.docker.effects, [])
        self.assertEqual(self.docker.queries, 0)

    def test_a_container_swapped_after_bind_refuses_before_the_effect(self):
        grant = self.bind()
        self.docker.containers[WP_ID[:12]][0] = WP_ID[:12] + "f" * 52
        row = {}
        store = SimpleNamespace(save=lambda value: None)
        with self.assertRaises(ShareRefusal):
            gate._execute(grant, "wp_admin", row, store, grant_id="l-" + "0" * 32,
                          wordpress={"token_hash": "0" * 64, "issued_at": 1, "expires_at": 2})
        self.assertEqual(self.docker.effects, [])
        self.assertEqual(row, {})

    def test_changed_bound_containers_refuse(self):
        def replaced():
            self.docker.containers[WP_ID[:12]][0] = WP_ID[:12] + "f" * 52

        def stopped():
            self.docker.containers[GATE_ID[:12]][1] = False

        def relabelled():
            self.docker.containers[DB_ID[:12]][3] = "other"

        def gone():
            del self.docker.containers[DB_ID[:12]]

        def new_gate_command():
            self.docker.containers[GATE_ID[:12]][5] = ["python3", "-c", "other"]

        def foreign_project():
            self.docker.containers[WP_ID[:12]][2] = "sandbox-host-other-production"

        for change in (replaced, stopped, relabelled, gone, new_gate_command, foreign_project):
            with self.subTest(change=change.__name__):
                self.setUp()
                grant = self.bind()
                change()
                with self.assertRaises(ShareRefusal):
                    grant.require_current()

    def test_changed_target_records_or_owner_refuse(self):
        def reapplied():
            self.summary["last_apply"] = {"status": "succeeded", "at": 2}

        def sharing_withdrawn():
            self.summary["owner_share"] = None

        def reaping():
            self.lifetime["reap_run"] = "r-1"

        def released():
            self.lifetime["state"] = "released"

        def new_copy():
            self.lifetime["copy_id"] = "ffffffff"

        def owner_changed():
            self.current_owner = SimpleNamespace(**{**vars(self.owner), "uid": 1001})

        for change in (reapplied, sharing_withdrawn, reaping, released, new_copy, owner_changed):
            with self.subTest(change=change.__name__):
                self.setUp()
                grant = self.bind()
                change()
                with self.assertRaises(ShareRefusal):
                    grant.require_current()

    def test_unchanged_lifetime_extension_does_not_refuse(self):
        grant = self.bind()
        self.lifetime["expires_at"] = 99
        grant.require_current()

    def test_bind_refuses_a_gate_running_another_loader(self):
        self.docker.containers[GATE_ID[:12]][5] = ["python3", "-c", "old loader"]
        with self.assertRaises(ShareRefusal) as raised:
            self.bind()
        self.assertEqual(raised.exception.code, "owner_share_upgrade_required")


if __name__ == "__main__":
    unittest.main()
