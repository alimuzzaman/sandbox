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
            GATE_ID[:12]: [GATE_ID, True, COMPOSE, "sandbox-login", "1", self.command, "False"],
            WP_ID[:12]: [WP_ID, True, COMPOSE, "wordpress", "1", ["apache2-foreground"], "False"],
            DB_ID[:12]: [DB_ID, True, COMPOSE, "db", "1", ["mysqld"], "False"],
        }
        # Running containers `docker ps` sees beyond the bound ones: [full id, project, oneoff].
        self.others = []
        self.reply = {"ok": True, "config_digest": access["gate_config_digest"],
                      "panel_access_digest": digest(declaration),
                      "panel_target_digest": access["target_digest"],
                      "capabilities": ["owner-panel-gate-v1"]}
        self.inspects = 0
        self.queries = 0
        self.effects = []

    def run(self, argv, **_):
        if argv[:2] == ["docker", "ps"]:
            rows = [(row[0], row[2], row[6]) for row in self.containers.values() if row[1] is True]
            rows += [tuple(row) for row in self.others]
            wanted = [item.split("=", 1)[1] for item in argv if item.startswith("label=")]
            ids = [ident for ident, project, oneoff in rows
                   if wanted == ["com.docker.compose.project=" + project, "com.docker.compose.oneoff=" + str(oneoff)]]
            return subprocess.CompletedProcess(argv, 0, "".join(i + "\n" for i in ids), "")
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
        child.poll = lambda: child.returncode
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
        self.recovery = {"version": 1, "hosts": {f"fixture/{PROJECT}/{ENV}": {}}}

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
        # Never observe a real host pid from a synthetic child.
        scope.enter_context(patch.object(gate._process, "capture_process_identity", return_value=None))
        scope.enter_context(patch.object(gate.login, "parse_site_reply", side_effect=json.loads))
        scope.enter_context(patch.object(gate.panel_records, "read_server_records",
                                         side_effect=lambda _: {"targets": [json.loads(json.dumps(self.summary))]}))
        scope.enter_context(patch.object(gate.panel_records, "_read_json",
                                         side_effect=lambda _: json.loads(json.dumps(self.summary))))
        scope.enter_context(patch.object(gate.panel_records, "parse_target_summary", side_effect=lambda value: value))
        scope.enter_context(patch("sandbox.hosting.lifetime.hoststore.Store.peek",
                                  side_effect=lambda *_: {"records": [dict(self.lifetime)], "invalid": [],
                                                          "truncated": False}))
        scope.enter_context(patch("sandbox.hosting.recovery.repository.RecoveryRepository.load",
                                  side_effect=lambda: json.loads(json.dumps(self.recovery))))
        self.request = self.admitted("create")

    @staticmethod
    def admitted(action):
        request = object.__new__(gate.PanelRequest)
        for key, value in zip(("action", "remote", "stack", "link_id", "_seal"),
                              (action, "fixture", SLUG, None, gate._SEAL)):
            object.__setattr__(request, key, value)
        return request

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

    def test_one_off_runs_neither_satisfy_nor_break_the_service_set(self):
        # A `docker run` of a Compose-built image carries the project label but
        # no oneoff=False; a `compose run` carries oneoff=True.
        self.docker.others = [("d" * 64, COMPOSE, None), ("e" * 64, COMPOSE, "True")]
        grant = self.bind()
        grant.require_current()

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
        store = SimpleNamespace(save=lambda value: None)
        # Each verb under an action its own validation admits, so only the fence refuses.
        for verb, action in (("mint", "create"), ("list", "list"), ("revoke", "revoke")):
            with self.subTest(verb=verb):
                fenced = gate.bind(self.admitted(action), gate_verbs=False)
                before = len(self.docker.effects)
                with self.assertRaises(ShareRefusal):
                    gate._execute(fenced, verb, {}, store, payload={}, grant_id="l-" + "0" * 32)
                self.assertEqual(len(self.docker.effects), before)
                # Control: a full grant passes the same validation and reaches the
                # gate (the fake reply is empty, so the effect itself is unknown).
                full = gate.bind(self.admitted(action))
                with self.assertRaises(ShareRefusal):
                    gate._execute(full, verb, {}, store, payload={"link": {}}, grant_id="l-" + "0" * 32)
                self.assertEqual(self.docker.effects[-1][:2], ["docker", "exec"])
                self.assertEqual(len(self.docker.effects), before + 1)

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

        def replacement_beside_the_original():
            # Every bound container keeps running; a new WordPress joins the project.
            self.docker.others = [("f" * 64, COMPOSE, "False")]

        for change in (replaced, stopped, relabelled, gone, new_gate_command, foreign_project,
                       replacement_beside_the_original):
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

        def recovery_fence_left_behind():
            # Persisted after bind; the process lock _held takes is free again.
            self.recovery["hosts"][f"fixture/{PROJECT}/{ENV}"] = {"active_operation": {"id": "op"}}

        def uncertain_recovery():
            self.recovery["hosts"][f"fixture/{PROJECT}/{ENV}"] = {"recovery_uncertainty": {"at": 1}}

        def new_alias_key():
            self.recovery["hosts"][f"alias/{PROJECT}/{ENV}"] = {}

        for change in (reapplied, sharing_withdrawn, reaping, released, new_copy, owner_changed,
                       recovery_fence_left_behind, uncertain_recovery, new_alias_key):
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



class _Store:
    def __init__(self):
        self.saved = []

    def save(self, row):
        self.saved.append(json.loads(json.dumps(row)))


BOOT = "0" * 8 + "-0000-0000-0000-" + "0" * 12


class SettleTests(GateRecheckTests):
    """Fable pass 2 [2]: a completed failure, or a restart mid-share, must settle."""

    def setUp(self):
        super().setUp()
        self.addCleanup(gate._LIVE.clear)
        self.clock = [1000.0]
        clock = patch.object(gate.time, "time", side_effect=lambda: self.clock[0])
        clock.start()
        self.addCleanup(clock.stop)

    def failing(self, *, exited):
        def popen(argv, **_):
            child = SimpleNamespace(pid=4242, returncode=1 if exited else None)
            def communicate(data=b"", timeout=None):
                if not exited:
                    raise subprocess.TimeoutExpired(argv, timeout)
                return b"", b"Error response from daemon\n"
            child.communicate = communicate
            child.poll = lambda: child.returncode
            return child
        return popen

    def run_failed_mint(self, *, exited, identity=None):
        grant = self.bind()
        row, store = {"id": "a-1", "phase": "minting"}, _Store()
        identity = identity or gate._process.ProcessIdentity(BOOT, 4242, "111", "")
        with patch.object(gate.subprocess, "Popen", side_effect=self.failing(exited=exited)), \
                patch.object(gate._process, "capture_process_identity", return_value=identity):
            with self.assertRaises(ShareRefusal) as raised:
                gate._execute(grant, "mint", row, store, payload={"link": {}}, grant_id="l-" + "1" * 12)
        self.assertEqual(raised.exception.code, "owner_share_effect_unknown")
        self.assertEqual(row["operation"]["state"], "unknown")
        return grant, row, store

    def test_a_failed_exec_settles_after_the_drain(self):
        grant, row, store = self.run_failed_mint(exited=True)
        gate.observe_original(grant, row, store)
        self.assertEqual(row["operation"]["state"], "unknown")  # still inside the drain
        self.clock[0] += gate.EFFECT_DRAIN_SECONDS
        gate.observe_original(grant, row, store)
        self.assertEqual(row["operation"]["state"], "settled")
        self.assertEqual(row["phase"], "cleanup_pending")
        self.assertNotIn(row["operation"]["id"], gate._LIVE)

    def test_restart_mid_share_settles_once_the_recorded_process_is_gone(self):
        grant, row, store = self.run_failed_mint(exited=False)
        gate._LIVE.clear()  # the panel restarted: no original handle
        with patch.object(gate._process, "process_absence_proven", return_value=False), \
                patch.object(gate._process, "capture_process_identity",
                             return_value=gate._process.ProcessIdentity(BOOT, 4242, "111", "")):
            self.clock[0] += 3600
            gate.observe_original(grant, row, store)
        self.assertEqual(row["operation"]["state"], "unknown")  # same process may still write
        with patch.object(gate._process, "process_absence_proven", return_value=True):
            gate.observe_original(grant, row, store)
            self.assertEqual(row["operation"]["state"], "unknown")
            self.clock[0] += gate.EFFECT_DRAIN_SECONDS
            gate.observe_original(grant, row, store)
        self.assertEqual(row["operation"]["state"], "settled")

    def test_a_reused_pid_or_new_boot_proves_the_original_gone(self):
        for observed in (gate._process.ProcessIdentity(BOOT, 4242, "222", ""),
                         gate._process.ProcessIdentity("1" + BOOT[1:], 4242, "111", "")):
            with self.subTest(observed=observed):
                grant, row, store = self.run_failed_mint(exited=False)
                gate._LIVE.clear()
                with patch.object(gate._process, "process_absence_proven", return_value=False), \
                        patch.object(gate._process, "capture_process_identity", return_value=observed):
                    gate.observe_original(grant, row, store)
                    self.clock[0] += gate.EFFECT_DRAIN_SECONDS
                    gate.observe_original(grant, row, store)
                self.assertEqual(row["operation"]["state"], "settled")

    def test_a_row_without_identity_settles_only_on_an_absent_pid(self):
        grant = self.bind()
        row = {"id": "a-1", "phase": "cleanup_pending",
               "operation": {"id": "o-x", "verb": "mint", "pid": 4242, "state": "unknown"}}
        self.clock[0] += 3600
        with patch.object(gate._process, "process_absence_proven", return_value=False):
            gate.observe_original(grant, row, _Store())
            self.clock[0] += gate.EFFECT_DRAIN_SECONDS
            gate.observe_original(grant, row, _Store())
        self.assertEqual(row["operation"]["state"], "unknown")
        with patch.object(gate._process, "process_absence_proven", return_value=True):
            gate.observe_original(grant, row, _Store())
            self.clock[0] += gate.EFFECT_DRAIN_SECONDS
            gate.observe_original(grant, row, _Store())
        self.assertEqual(row["operation"]["state"], "settled")


if __name__ == "__main__":
    unittest.main()
