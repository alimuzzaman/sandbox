import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sandbox.core._cloudflare as cloudflare
from sandbox.hosting import destroy
from tests.subprocess_support import run_test_process

ENTRY = {"name": "r1", "host": "h", "user": "u", "front_door": "nginx"}
KEY = "r1/site/sandbox"
EMPTY_PROBE = {"containers": 0, "volumes": 0, "networks": 0, "images": 0,
               "retained_images": 0, "retained_untagged": 0, "retained_refs": [],
               "runtime_kb": 0, "source_kb": 0, "route": 0, "certs": 0}


def _record(**extra):
    return {"compose_project": "sandbox-host-site-sandbox", "front_door": "nginx",
            "records": [{"zone_id": "z1", "created_id": "rec1", "previous": None}],
            "certificate": {"id": "cert1", "hostnames": ["wp-site.example"]}, **extra}


class TargetParsing(unittest.TestCase):
    def test_target_needs_project_and_environment(self):
        self.assertEqual(destroy.parse_target("site/sandbox"), ("site", "sandbox"))
        for bad in ("", "site", "site/sandbox/x", "../x/y", "Site/sandbox", "site/ "):
            with self.subTest(bad=bad), self.assertRaises(destroy.DestroyError):
                destroy.parse_target(bad)

    def test_incomplete_probe_refuses(self):
        with self.assertRaises(destroy.DestroyError):
            destroy.parse_probe("containers=0\nvolumes=0")
        parsed = destroy.parse_probe("containers=2\nvolumes=1\nnetworks=0\nimages=1\n"
                                     "retained_images=0\nretained_untagged=0\n"
                                     "runtime_kb=\nsource_kb=10\nroute=1\ncerts=0\n")
        self.assertEqual(parsed["containers"], 2)
        self.assertEqual(parsed["runtime_kb"], 0)

    def test_probe_requires_the_retained_counts(self):
        text = ("containers=2\nvolumes=1\nnetworks=0\nimages=1\n"
                "retained_untagged=0\nruntime_kb=\nsource_kb=10\nroute=1\ncerts=0\n")
        with self.assertRaises(destroy.DestroyError) as caught:
            destroy.parse_probe(text)
        self.assertEqual(caught.exception.code, "remote_probe_failed")

    def test_probe_collects_retained_references(self):
        lines = [
            "containers=0", "volumes=0", "networks=0", "images=0",
            "retained_images=25", "retained_untagged=2",
            "runtime_kb=0", "source_kb=0", "route=0", "certs=0",
        ]
        for n in range(25):
            lines.append(f"retained_image=r{n}:1")
        parsed = destroy.parse_probe("\n".join(lines) + "\n")
        self.assertEqual(parsed["retained_images"], 25)
        self.assertEqual(parsed["retained_untagged"], 2)
        self.assertEqual(len(parsed["retained_refs"]), 20)
        self.assertEqual(parsed["retained_refs"], [f"r{n}:1" for n in range(20)])


@mock.patch.object(destroy.remote, "resolve_sandbox_home", lambda entry: "/home/u/sandbox")
class Planning(unittest.TestCase):
    def test_plan_lists_recorded_cloudflare_objects_and_paths(self):
        state = {"hosts": {KEY: _record()}}
        found = destroy.plan(state, ENTRY, "r1", "site", "sandbox", probe=dict(EMPTY_PROBE))
        self.assertTrue(found["found"])
        self.assertEqual(found["dns_records"], [{"zone_id": "z1", "action": "delete", "record_id": "rec1", "verifiable": False}])
        self.assertEqual(found["origin_certificate"], "cert1")
        self.assertEqual(found["remote"]["runtime_dir"], "/home/u/sandbox/runtime/hosts/site/sandbox")
        self.assertEqual(found["remote"]["route_file"], "/etc/nginx/conf.d/sandbox-host-site-sandbox.conf")

    def test_an_existing_record_is_restored_not_deleted(self):
        record = _record(records=[{"zone_id": "z1", "created_id": None, "previous": {"id": "old"}}])
        found = destroy.plan({"hosts": {KEY: record}}, ENTRY, "r1", "site", "sandbox",
                             probe=dict(EMPTY_PROBE))
        self.assertEqual(found["dns_records"][0]["action"], "restore_previous")

    def test_other_environments_are_reported(self):
        state = {"hosts": {KEY: _record(), "r1/site/production": {}}}
        found = destroy.plan(state, ENTRY, "r1", "site", "sandbox", probe=dict(EMPTY_PROBE))
        self.assertEqual(found["other_environments"], ["r1/site/production"])

    def test_unreadable_namesake_state_refuses(self):
        from sandbox.hosting.state_partition.layout import HostState, digest16
        state = HostState({"hosts": {KEY: _record()}})
        for owned in ("r1/site/sandbox", "r1/site-blue/sandbox"):
            state.unreadable = frozenset({digest16(owned)})
            project, environment = ("site", "sandbox") if owned == KEY else ("site", "blue-sandbox")
            with self.assertRaises(destroy.DestroyError) as caught:
                destroy.plan(state, ENTRY, "r1", project, environment, probe=dict(EMPTY_PROBE))
            self.assertEqual(caught.exception.code, "state_unreadable")

    def test_unknown_target_with_nothing_remote_is_not_found(self):
        found = destroy.plan({"hosts": {}}, ENTRY, "r1", "site", "sandbox", probe=dict(EMPTY_PROBE))
        self.assertFalse(found["found"])

    def test_remote_only_leftovers_are_found_without_state(self):
        found = destroy.plan({"hosts": {}}, ENTRY, "r1", "site", "sandbox",
                             probe={**EMPTY_PROBE, "volumes": 2})
        self.assertTrue(found["found"])
        self.assertFalse(found["state_entry"])

    def test_a_colliding_compose_name_refuses(self):
        state = {"hosts": {"r1/site-blue/sandbox": {"compose_project": "sandbox-host-site-blue-sandbox"}}}
        with self.assertRaises(destroy.DestroyError) as caught:
            destroy.plan(state, ENTRY, "r1", "site", "blue-sandbox", probe=dict(EMPTY_PROBE))
        self.assertEqual(caught.exception.code, "target_ambiguous")

    def test_a_failed_docker_listing_fails_the_probe(self):
        command = destroy._probe_command("sandbox-host-site-sandbox",
                                         destroy._paths("/h", "site", "sandbox"), "/r", ["/c"])
        result = run_test_process(["bash", "-c", "docker() { return 1; }; " + command],
                                  capture_output=True, text=True)
        self.assertEqual(result.returncode, 3)

    def test_namesakes_cover_every_split_of_the_compose_name(self):
        self.assertEqual(destroy.namesakes("r1", "site-blue", "sandbox"),
                         ["r1/site-blue/sandbox", "r1/site/blue-sandbox"])
        self.assertEqual(destroy.namesakes("r1", "site", "prod"), ["r1/site/prod"])

    def test_caddy_front_door_refuses(self):
        with self.assertRaises(destroy.DestroyError) as caught:
            destroy.plan({"hosts": {KEY: _record(front_door="caddy")}}, ENTRY, "r1", "site",
                         "sandbox", probe=dict(EMPTY_PROBE))
        self.assertEqual(caught.exception.code, "front_door_capability_unavailable")

    def test_network_only_leftovers_are_found(self):
        found = destroy.plan({"hosts": {}}, ENTRY, "r1", "site", "sandbox",
                             probe={**EMPTY_PROBE, "networks": 1})
        self.assertTrue(found["found"])

    def test_mismatched_compose_project_refuses(self):
        record = _record(compose_project="sandbox-host-other-sandbox")
        with self.assertRaises(destroy.DestroyError):
            destroy.plan({"hosts": {KEY: record}}, ENTRY, "r1", "site", "sandbox",
                         probe=dict(EMPTY_PROBE))

    def test_fences_are_reported(self):
        found = destroy.plan({"hosts": {KEY: _record(active_operation={"id": "x"})}}, ENTRY,
                             "r1", "site", "sandbox", probe=dict(EMPTY_PROBE))
        self.assertEqual(found["fences"], ["active_operation"])

    def test_retained_images_alone_are_nothing_to_remove(self):
        probe = {**EMPTY_PROBE, "retained_images": 2, "retained_untagged": 1,
                 "retained_refs": ["a/b:1", "c/d:2"]}
        found = destroy.plan({"hosts": {}}, ENTRY, "r1", "site", "sandbox", probe=probe)
        self.assertFalse(found["found"])
        rendered = destroy.render(found, confirmed=False)
        self.assertEqual(
            rendered,
            "r1/site/sandbox: nothing to remove; kept 2 labelled images not proven ours: a/b:1, c/d:2, 1 untagged",
        )

    def test_more_retained_images_than_listed(self):
        refs = [f"img/{i}:1" for i in range(20)]
        probe = {**EMPTY_PROBE, "retained_images": 22, "retained_untagged": 0, "retained_refs": refs}
        found = destroy.plan({"hosts": {}}, ENTRY, "r1", "site", "sandbox", probe=probe)
        rendered = destroy.render(found, confirmed=False)
        self.assertIn("(+2 more)", rendered)

    def test_dry_run_prints_kept_before_the_dry_run_line(self):
        probe = {**EMPTY_PROBE, "containers": 1, "retained_images": 1,
                 "retained_refs": ["shared/base:1"]}
        found = destroy.plan({"hosts": {KEY: _record()}}, ENTRY, "r1", "site", "sandbox", probe=probe)
        rendered = destroy.render(found, confirmed=False)
        lines = rendered.splitlines()
        kept_idx = next(i for i, line in enumerate(lines) if "kept 1 labelled images not proven ours" in line)
        dry_idx = next(i for i, line in enumerate(lines) if "dry run; re-run with --confirm to remove" in line)
        self.assertLess(kept_idx, dry_idx)


class FakeClient:
    def __init__(self, missing=()):
        self.calls = []
        self.missing = set(missing)
        self.current = {}

    def record(self, zone_id, record_id):
        return self.current.get(record_id)

    def restore_record(self, zone_id, previous, created_id=None):
        self.calls.append(("dns", zone_id, created_id))
        if "dns" in self.missing:
            raise cloudflare.CloudflareError("Cloudflare request failed: HTTP 404: not found")

    def revoke_origin_certificate(self, certificate_id):
        self.calls.append(("cert", certificate_id))
        if "cert" in self.missing:
            raise cloudflare.CloudflareError("Cloudflare request failed: HTTP 404: not found")


@mock.patch.object(destroy.remote, "resolve_sandbox_home", lambda entry: "/home/u/sandbox")
class Execution(unittest.TestCase):
    def _run(self, record, ssh_outputs, client, extra_hosts=None, **kwargs):
        from sandbox.hosting.recovery.repository import RecoveryRepository
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        repository = RecoveryRepository(root / "hosts.json", root / "locks")
        hosts = {KEY: record, "r1/keep/sandbox": {"x": 1}}
        if extra_hosts:
            hosts.update(extra_hosts)
        repository._write({"version": 1, "hosts": hosts})
        self._last_state = lambda: real_load(root / "hosts.json")
        replies = iter(ssh_outputs)
        commands = []

        def ssh(entry, command, timeout=30):
            commands.append(command)
            reply = next(replies)
            code, out = reply if isinstance(reply, tuple) else (0, reply)
            return subprocess.CompletedProcess([], code, out, "")

        real_load = destroy.hosting.load_host_state
        load = lambda: real_load(root / "hosts.json")
        with mock.patch.object(destroy.remote, "ssh_run", ssh), \
                mock.patch("sandbox.hosting.recovery.repository.RecoveryRepository",
                           lambda: repository), \
                mock.patch.object(destroy.hosting, "load_host_state", load):
            result = destroy.execute(ENTRY, "r1", "site", "sandbox",
                                     client_factory=lambda: client, **kwargs)
        return result, commands, load()

    PROBE = "containers=1\nvolumes=2\nnetworks=1\nimages=1\nretained_images=0\nretained_untagged=0\nruntime_kb=24\nsource_kb=9\nroute=0\ncerts=0\n"

    def test_removes_remote_then_cloudflare_then_state(self):
        client = FakeClient()
        result, commands, state = self._run(
            _record(), [self.PROBE, "", "removed\n"], client)
        self.assertEqual(result["state"], "destroyed")
        self.assertNotIn("docker compose", commands[2])
        self.assertNotIn("deploy-src", commands[2])
        self.assertIn("/home/u/sandbox/runtime/hosts/site/sandbox", commands[2])
        self.assertEqual(client.calls, [("dns", "z1", "rec1"), ("cert", "cert1")])
        self.assertNotIn(KEY, state["hosts"])
        self.assertIn("r1/keep/sandbox", state["hosts"])

    def test_already_deleted_cloudflare_objects_count_as_removed(self):
        client = FakeClient(missing={"dns", "cert"})
        result, _, state = self._run(_record(), [self.PROBE, "", "removed\n"], client)
        self.assertEqual(result["cloudflare"], [{"zone_id": "z1", "state": "already_absent"},
                                                {"origin_certificate": "already_absent"}])
        self.assertNotIn(KEY, state["hosts"])

    def test_an_already_revoked_certificate_counts_as_removed(self):
        client = FakeClient()
        def revoked(certificate_id):
            raise cloudflare.CloudflareError(
                "Cloudflare request failed: HTTP 400: Certificate already revoked")
        client.revoke_origin_certificate = revoked
        result, _, state = self._run(_record(), [self.PROBE, "", "removed\n"], client)
        self.assertEqual(result["cloudflare"][-1], {"origin_certificate": "already_absent"})
        self.assertNotIn(KEY, state["hosts"])

    def test_leftover_volumes_keep_state_and_skip_cloudflare(self):
        client = FakeClient()
        with self.assertRaises(destroy.DestroyError):
            self._run(_record(), [self.PROBE, "", "containers=0\nvolumes=1\n"], client)
        self.assertEqual(client.calls, [])

    def test_failed_teardown_keeps_state_and_skips_cloudflare(self):
        client = FakeClient()
        with self.assertRaises(destroy.DestroyError) as caught:
            self._run(_record(), [self.PROBE, "", (3, "")], client)
        self.assertEqual(caught.exception.code, "remote_teardown_incomplete")
        self.assertEqual(client.calls, [])

    def test_interrupted_teardown_leaves_a_durable_fence(self):
        with self.assertRaises(destroy.DestroyError):
            self._run(_record(), [self.PROBE, "", (124, "")], FakeClient())
        state = self._last_state()
        self.assertEqual(state["hosts"][KEY]["active_operation"]["action"], "destroy")
        found = destroy.plan(state, ENTRY, "r1", "site", "sandbox", probe=dict(EMPTY_PROBE))
        self.assertIn("active_operation", found["fences"])

    def test_interrupted_teardown_fences_every_colliding_name_and_retry_clears_them(self):
        with self.assertRaises(destroy.DestroyError):
            self._run(_record(), [self.PROBE, "", (124, "")], FakeClient())
        state = self._last_state()
        for owned in destroy.namesakes("r1", "site", "sandbox"):
            self.assertEqual(state["hosts"][owned]["active_operation"]["action"], "destroy")
        found = destroy.plan(state, ENTRY, "r1", "site", "sandbox", probe=dict(EMPTY_PROBE))
        self.assertIn("active_operation", found["fences"])

    def test_retry_with_clear_fenced_removes_the_placeholders(self):
        record = _record(active_operation={"action": "destroy", "started_at": 1})
        result, _, state = self._run(record, [self.PROBE, "", "removed\n"], FakeClient(),
                                     clear_fenced=True)
        self.assertEqual(result["state"], "destroyed")
        for owned in destroy.namesakes("r1", "site", "sandbox"):
            self.assertNotIn(owned, state["hosts"])
        self.assertIn("r1/keep/sandbox", state["hosts"])

    def test_certificate_cleanup_reloads_nginx(self):
        client = FakeClient()
        _, commands, _ = self._run(_record(), [self.PROBE, "", "removed\n"], client)
        self.assertIn("systemctl reload nginx", commands[1])

    def test_failed_certificate_cleanup_keeps_state(self):
        client = FakeClient()
        with self.assertRaises(destroy.DestroyError) as caught:
            self._run(_record(), [self.PROBE, (1, "")], client)
        self.assertEqual(caught.exception.code, "edge_cleanup_failed")
        self.assertEqual(client.calls, [])

    def test_certificates_are_cleaned_even_when_the_route_is_gone(self):
        _, commands, _ = self._run(_record(), [self.PROBE, "", "removed\n"], FakeClient())
        self.assertIn("rm -rf /etc/sandbox-edge/certs/host-site-sandbox", commands[1])
        self.assertIn("certbot delete", commands[1])

    def test_teardown_fails_when_docker_is_unreachable(self):
        command = destroy._remote_teardown_command(
            "sandbox-host-site-sandbox", destroy._paths("/nonexistent", "site", "sandbox"))
        result = run_test_process(["bash", "-c", "docker() { return 1; }; " + command],
                                  capture_output=True, text=True)
        self.assertEqual(result.returncode, 3)

    def test_teardown_removes_only_its_own_image_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "rmi.log"
            fake = root / "bin" / "docker"
            fake.parent.mkdir()
            fake.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  images) printf '%s\\n' sandbox-host-site-sandbox-web:latest shared/base:1 ;;\n"
                "  rmi) echo \"$@\" >> \"$LOG\" ;;\n"
                "esac\n")
            fake.chmod(0o755)
            command = destroy._remote_teardown_command(
                "sandbox-host-site-sandbox", destroy._paths(directory, "site", "sandbox"))
            result = run_test_process(
                ["bash", "-c", command],
                env={"LOG": str(log), "PATH": f"{fake.parent}:/usr/bin:/bin"},
                capture_output=True, text=True)
            # The fake still lists the tag afterwards, so verification refuses.
            self.assertEqual(result.returncode, 7, result.stderr)
            removed = log.read_text().split()
        self.assertEqual(removed, ["rmi", "sandbox-host-site-sandbox-web:latest"])

    def test_probe_and_teardown_agree_on_owned_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "rmi.log"
            fake = root / "bin" / "docker"
            fake.parent.mkdir()
            fake.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  images) printf '%s\\n' sandbox-host-site-sandbox-web:latest shared/base:1 '<none>:<none>' ;;\n"
                "  rmi) echo \"$@\" >> \"$LOG\" ;;\n"
                "  ps|volume|network) ;;\n"
                "esac\n")
            fake.chmod(0o755)
            sudo_shim = root / "bin" / "sudo"
            sudo_shim.write_text("#!/bin/sh\nexec \"$@\"\n")
            sudo_shim.chmod(0o755)

            paths = destroy._paths(directory, "site", "sandbox")
            command = destroy._probe_command(
                "sandbox-host-site-sandbox", paths, "/nonexistent/route", ["/nonexistent/cert"])
            result = run_test_process(
                ["bash", "-c", command],
                env={"LOG": str(log), "PATH": f"{fake.parent}:/usr/bin:/bin"},
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            parsed = destroy.parse_probe(result.stdout)
            self.assertEqual(parsed["images"], 1)
            self.assertEqual(parsed["retained_images"], 1)
            self.assertEqual(parsed["retained_untagged"], 1)
            self.assertEqual(parsed["retained_refs"], ["shared/base:1"])

            teardown_cmd = destroy._remote_teardown_command(
                "sandbox-host-site-sandbox", paths)
            run_test_process(
                ["bash", "-c", teardown_cmd],
                env={"LOG": str(log), "PATH": f"{fake.parent}:/usr/bin:/bin"},
                capture_output=True, text=True)
            removed = log.read_text().split()
            self.assertEqual(removed, ["rmi", "sandbox-host-site-sandbox-web:latest"])

    def test_retained_reference_with_unsafe_characters_is_counted_not_listed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "rmi.log"
            fake = root / "bin" / "docker"
            fake.parent.mkdir()
            fake.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  images) printf '%s\\n' 'evil$(x):1' ;;\n"
                "  rmi) echo \"$@\" >> \"$LOG\" ;;\n"
                "  ps|volume|network) ;;\n"
                "esac\n")
            fake.chmod(0o755)
            sudo_shim = root / "bin" / "sudo"
            sudo_shim.write_text("#!/bin/sh\nexec \"$@\"\n")
            sudo_shim.chmod(0o755)

            paths = destroy._paths(directory, "site", "sandbox")
            command = destroy._probe_command(
                "sandbox-host-site-sandbox", paths, "/nonexistent/route", ["/nonexistent/cert"])
            result = run_test_process(
                ["bash", "-c", command],
                env={"LOG": str(log), "PATH": f"{fake.parent}:/usr/bin:/bin"},
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            parsed = destroy.parse_probe(result.stdout)
            self.assertEqual(parsed["retained_images"], 1)
            self.assertEqual(parsed["retained_refs"], [])

    def test_fenced_target_refuses_before_any_effect(self):
        client = FakeClient()
        with self.assertRaises(destroy.DestroyError) as caught:
            self._run(_record(active_operation={"id": "x"}), [self.PROBE], client)
        self.assertEqual(caught.exception.code, "target_fenced")
        self.assertEqual(client.calls, [])

    def test_unchanged_record_is_deleted(self):
        client = FakeClient()
        applied = {
            "name": "a.test", "type": "A", "content": "192.0.2.1",
            "proxied": True, "ttl": 1, "comment": "managed by Sandbox hosting",
        }
        client.current = {"x": {"id": "x", **applied}}
        record = _record(records=[{
            "zone_id": "z1", "created_id": "x", "previous": None,
            "applied": dict(applied),
        }])
        restored = []
        orig_restore = client.restore_record
        def track_restore(zone_id, previous, created_id=None):
            restored.append((zone_id, previous, created_id))
            return orig_restore(zone_id, previous, created_id)
        client.restore_record = track_restore
        result, _, state = self._run(record, [self.PROBE, "", "removed\n"], client)
        self.assertEqual(result["state"], "destroyed")
        self.assertEqual(restored, [("z1", None, "x")])
        self.assertNotIn(KEY, state["hosts"])

    def test_unchanged_adopted_record_is_restored(self):
        client = FakeClient()
        applied = {
            "name": "a.test", "type": "A", "content": "192.0.2.1",
            "proxied": True, "ttl": 1, "comment": "managed by Sandbox hosting",
        }
        client.current = {"x": {"id": "x", **applied}}
        prev = {
            "id": "x", "type": "A", "name": "a.test", "content": "198.51.100.9",
            "proxied": True, "ttl": 1, "comment": "managed by Sandbox hosting",
        }
        record = _record(records=[{
            "zone_id": "z1", "created_id": "x", "previous": prev,
            "applied": dict(applied),
        }])
        restored = []
        orig_restore = client.restore_record
        def track_restore(zone_id, previous, created_id=None):
            restored.append((zone_id, previous, created_id))
            return orig_restore(zone_id, previous, created_id)
        client.restore_record = track_restore
        result, _, state = self._run(record, [self.PROBE, "", "removed\n"], client)
        self.assertEqual(result["state"], "destroyed")
        self.assertEqual(restored, [("z1", prev, "x")])
        self.assertNotIn(KEY, state["hosts"])

    def test_changed_record_is_left_and_reported(self):
        applied = {
            "name": "a.test", "type": "A", "content": "192.0.2.1",
            "proxied": True, "ttl": 1, "comment": "managed by Sandbox hosting",
        }
        variations = {
            "content": "203.0.113.5",
            "type": "AAAA",
            "name": "renamed.test",
            "proxied": False,
            "ttl": 300,
            "comment": "hand-edited",
        }
        for field, new_val in variations.items():
            with self.subTest(field=field):
                client = FakeClient()
                client.current = {"x": {"id": "x", **applied, field: new_val}}
                record = _record(records=[{
                    "zone_id": "z1", "created_id": "x", "previous": None,
                    "applied": dict(applied),
                }])
                result, _, state = self._run(record, [self.PROBE, "", "removed\n"], client)
                self.assertEqual(result["state"], "destroyed")
                self.assertEqual([call for call in client.calls if call[0] == "dns"], [])
                self.assertIn({"zone_id": "z1", "state": "kept_changed", "record_id": "x"},
                              result["cloudflare"])
                rendered = destroy.render(result, confirmed=True)
                self.assertIn("left in place", rendered)
                self.assertIn("x", rendered)
                self.assertIn("z1", rendered)
                self.assertNotIn(KEY, state["hosts"])

    def test_vanished_record_is_not_recreated(self):
        client = FakeClient()
        client.current = {}
        applied = {
            "name": "a.test", "type": "A", "content": "192.0.2.1",
            "proxied": True, "ttl": 1, "comment": "managed by Sandbox hosting",
        }
        prev = {
            "id": "x", "type": "A", "name": "a.test", "content": "198.51.100.9",
            "proxied": True, "ttl": 1, "comment": "managed by Sandbox hosting",
        }
        record = _record(records=[{
            "zone_id": "z1", "created_id": "x", "previous": prev,
            "applied": dict(applied),
        }])
        result, _, state = self._run(record, [self.PROBE, "", "removed\n"], client)
        self.assertEqual(result["state"], "destroyed")
        self.assertEqual([call for call in client.calls if call[0] == "dns"], [])
        self.assertIn({"zone_id": "z1", "state": "already_absent", "record_id": "x"}, result["cloudflare"])
        rendered = destroy.render(result, confirmed=True)
        self.assertIn("was already gone", rendered)
        self.assertIn("x", rendered)
        self.assertIn("z1", rendered)
        self.assertNotIn(KEY, state["hosts"])

    def test_renamed_record_is_not_called_absent(self):
        client = FakeClient()
        applied = {
            "name": "a.test", "type": "A", "content": "192.0.2.1",
            "proxied": True, "ttl": 1, "comment": "managed by Sandbox hosting",
        }
        client.current = {"x": {"id": "x", **applied, "name": "renamed.test"}}
        record = _record(records=[{
            "zone_id": "z1", "created_id": "x", "previous": None,
            "applied": dict(applied),
        }])
        result, _, state = self._run(record, [self.PROBE, "", "removed\n"], client)
        self.assertEqual(result["state"], "destroyed")
        self.assertEqual([call for call in client.calls if call[0] == "dns"], [])
        self.assertIn({"zone_id": "z1", "state": "kept_changed", "record_id": "x"}, result["cloudflare"])
        states = [row.get("state") for row in result.get("cloudflare", [])]
        self.assertNotIn("already_absent", states)
        self.assertNotIn(KEY, state["hosts"])

    def _destroy_row(self, row, current):
        """One saved DNS row through the real removal; returns (client, results)."""
        client = FakeClient()
        client.current = current
        results = destroy._remove_cloudflare({"_records": [row], "origin_certificate": None}, client)
        return client, results

    def test_previous_id_differing_from_the_verified_record_is_left_alone(self):
        applied = {"name": "a.test", "type": "A", "content": "192.0.2.1",
                   "proxied": True, "ttl": 1, "comment": "c"}
        row = {"zone_id": "z1", "created_id": "b", "applied": applied,
               "previous": {"id": "a", "type": "A", "name": "a.test", "content": "198.51.100.9"}}
        client, results = self._destroy_row(row, {"b": dict(applied, id="b")})
        self.assertEqual(results, [{"zone_id": "z1", "state": "kept_unverifiable", "record_id": "b"}])

    def test_empty_snapshot_and_missing_id_are_left_alone(self):
        for row in ({"zone_id": "z1", "created_id": "x", "applied": {}, "previous": None},
                    {"zone_id": "z1", "created_id": None, "applied": {
                        "name": "a.test", "type": "A", "content": "192.0.2.1"},
                     "previous": {"id": "x"}}):
            with self.subTest(row=row):
                _client, results = self._destroy_row(row, {})
                self.assertEqual(results[0]["state"], "kept_unverifiable")

    def test_dry_run_shows_untrusted_snapshots_as_kept(self):
        for row in ({"zone_id": "z1", "created_id": "x", "applied": {}, "previous": None},
                    {"zone_id": "z1", "created_id": "b", "previous": {"id": "a"},
                     "applied": {"name": "a.test", "type": "A", "content": "192.0.2.1"}}):
            with self.subTest(row=row):
                found = destroy.plan({"hosts": {KEY: _record(records=[row])}}, ENTRY, "r1", "site",
                                     "sandbox", probe=dict(EMPTY_PROBE))
                self.assertEqual(found["dns_records"][0]["action"], "keep_unverifiable")
                self.assertFalse(found["dns_records"][0]["verifiable"])

    def test_incomplete_applied_is_left_alone(self):
        client = FakeClient()
        record_called = []
        client.record = lambda zone_id, record_id: record_called.append((zone_id, record_id)) or None
        record = _record(records=[{
            "zone_id": "z1", "created_id": "x", "previous": None,
            "applied": {"name": "a.test", "type": "A"},
        }])
        result, _, state = self._run(record, [self.PROBE, "", "removed\n"], client)
        self.assertEqual(result["state"], "destroyed")
        self.assertEqual(record_called, [])
        self.assertEqual([call for call in client.calls if call[0] == "dns"], [])
        self.assertIn({"zone_id": "z1", "state": "kept_unverifiable", "record_id": "x"}, result["cloudflare"])
        self.assertNotIn(KEY, state["hosts"])

    def test_record_without_applied_keeps_the_old_behaviour(self):
        client = FakeClient()
        record_called = []
        client.record = lambda zone_id, record_id: record_called.append((zone_id, record_id)) or None
        record = _record(records=[{"zone_id": "z1", "created_id": "x", "previous": None}])
        result, _, state = self._run(record, [self.PROBE, "", "removed\n"], client)
        self.assertEqual(result["state"], "destroyed")
        self.assertEqual(record_called, [])
        self.assertIn(("dns", "z1", "x"), client.calls)
        self.assertNotIn(KEY, state["hosts"])

    def test_record_held_by_another_target_is_left_alone(self):
        client = FakeClient()
        applied = {
            "name": "a.test", "type": "A", "content": "192.0.2.1",
            "proxied": True, "ttl": 1, "comment": "managed by Sandbox hosting",
        }
        client.current = {"x": {"id": "x", **applied}}
        shared_records = [
            {"zone_id": "z1", "created_id": "x", "previous": None, "applied": dict(applied)},
            {"zone_id": "z2", "created_id": "x", "previous": None, "applied": dict(applied)},
        ]
        record = _record(records=shared_records)
        extra_hosts = {
            "r2/other/sandbox": {
                "records": [{"zone_id": "z1", "created_id": "x"}],
            }
        }
        state = {"hosts": {KEY: record, **extra_hosts}}
        planned = destroy.plan(state, ENTRY, "r1", "site", "sandbox", probe=dict(EMPTY_PROBE))
        dns_actions = {row["zone_id"]: row["action"] for row in planned["dns_records"]}
        self.assertEqual(dns_actions["z1"], "keep_shared")
        self.assertEqual(dns_actions["z2"], "delete")

        result, _, end_state = self._run(
            record, [self.PROBE, "", "removed\n"], client, extra_hosts=extra_hosts)
        self.assertEqual(result["state"], "destroyed")
        dns_calls = [call for call in client.calls if call[0] == "dns"]
        self.assertNotIn(("dns", "z1", "x"), dns_calls)
        self.assertIn(("dns", "z2", "x"), dns_calls)
        self.assertNotIn(KEY, end_state["hosts"])

    def test_previous_only_row_held_elsewhere_is_left_alone(self):
        # Case 1: this target has previous-only row, other target has created_id
        client = FakeClient()
        row_prev = {"zone_id": "z1", "created_id": None, "previous": {"id": "x"}}
        record = _record(records=[row_prev])
        extra_hosts = {
            "r2/other/sandbox": {
                "records": [{"zone_id": "z1", "created_id": "x", "previous": None}],
            }
        }
        state = {"hosts": {KEY: record, **extra_hosts}}
        planned = destroy.plan(state, ENTRY, "r1", "site", "sandbox", probe=dict(EMPTY_PROBE))
        self.assertEqual(planned["dns_records"][0]["action"], "keep_shared")
        self.assertEqual(planned["dns_records"][0]["record_id"], "x")

        result, _, end_state = self._run(
            record, [self.PROBE, "", "removed\n"], client, extra_hosts=extra_hosts)
        self.assertEqual(result["state"], "destroyed")
        self.assertEqual([call for call in client.calls if call[0] == "dns"], [])
        self.assertNotIn(KEY, end_state["hosts"])

        # Case 2 (reverse): this target has created_id, other target has previous-only row
        client = FakeClient()
        applied = {
            "name": "a.test", "type": "A", "content": "192.0.2.1",
            "proxied": True, "ttl": 1, "comment": "managed by Sandbox hosting",
        }
        row_created = {
            "zone_id": "z1", "created_id": "x", "previous": None,
            "applied": dict(applied),
        }
        record2 = _record(records=[row_created])
        extra_hosts2 = {
            "r2/other/sandbox": {
                "records": [{"zone_id": "z1", "created_id": None, "previous": {"id": "x"}}],
            }
        }
        state2 = {"hosts": {KEY: record2, **extra_hosts2}}
        planned2 = destroy.plan(state2, ENTRY, "r1", "site", "sandbox", probe=dict(EMPTY_PROBE))
        self.assertEqual(planned2["dns_records"][0]["action"], "keep_shared")
        self.assertEqual(planned2["dns_records"][0]["record_id"], "x")

        result2, _, end_state2 = self._run(
            record2, [self.PROBE, "", "removed\n"], client, extra_hosts=extra_hosts2)
        self.assertEqual(result2["state"], "destroyed")
        self.assertEqual([call for call in client.calls if call[0] == "dns"], [])
        self.assertNotIn(KEY, end_state2["hosts"])

    def test_cloudflare_read_failure_keeps_state(self):
        client = FakeClient()
        client.record = mock.Mock(side_effect=cloudflare.CloudflareError("Cloudflare read failed"))
        applied = {
            "name": "a.test", "type": "A", "content": "192.0.2.1",
            "proxied": True, "ttl": 1, "comment": "managed by Sandbox hosting",
        }
        record = _record(records=[{
            "zone_id": "z1", "created_id": "x", "previous": None,
            "applied": dict(applied),
        }])
        with self.assertRaises(cloudflare.CloudflareError):
            self._run(record, [self.PROBE, "", "removed\n"], client)
        self.assertIn(KEY, self._last_state()["hosts"])

    def test_render_lists_each_dns_action(self):
        result = {
            "target": KEY,
            "compose_project": "sandbox-host-site-sandbox",
            "front_door": "nginx",
            "remote": {"containers": 0, "volumes": 0, "images": 0, "route": 0, "certs": 0, "runtime_kb": 0},
            "origin_certificate": None,
            "state_entry": True,
            "other_environments": [],
            "fences": [],
            "dns_records": [
                {"zone_id": "z1", "action": "delete", "record_id": "rec-del", "verifiable": True},
                {"zone_id": "z1", "action": "restore_previous", "record_id": "rec-rest", "verifiable": True},
                {"zone_id": "z1", "action": "keep_shared", "record_id": "rec-shared", "verifiable": False},
            ],
        }
        text = destroy.render(result, confirmed=False)
        self.assertIn("DNS delete rec-del", text)
        self.assertIn("DNS restore_previous rec-rest", text)
        self.assertIn("DNS keep_shared rec-shared", text)
        self.assertEqual(text.count("check it by hand"), 1)

    def test_clear_fenced_allows_a_stale_record(self):
        client = FakeClient()
        result, _, _ = self._run(_record(active_operation={"id": "x"}),
                                 [self.PROBE, "", "removed\n"], client,
                                 clear_fenced=True)
        self.assertEqual(result["state"], "destroyed")

    def test_confirmed_destroy_prints_kept_after_removed(self):
        probe = ("containers=1\nvolumes=0\nnetworks=0\nimages=1\n"
                 "retained_images=1\nretained_untagged=0\nruntime_kb=0\nsource_kb=0\nroute=0\ncerts=0\n"
                 "retained_image=shared/base:1\n")
        client = FakeClient()
        result, _, _ = self._run(_record(), [probe, "", "removed\n"], client)
        self.assertEqual(result["state"], "destroyed")
        rendered = destroy.render(result, confirmed=True)
        lines = rendered.splitlines()
        removed_idx = next(i for i, line in enumerate(lines) if line == "removed")
        kept_idx = next(i for i, line in enumerate(lines) if "kept 1 labelled images not proven ours" in line)
        self.assertLess(removed_idx, kept_idx)

    def test_absent_target_reports_retained_images(self):
        from sandbox.hosting.recovery.repository import RecoveryRepository
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        repository = RecoveryRepository(root / "hosts.json", root / "locks")
        repository._write({"version": 1, "hosts": {"r1/keep/sandbox": {"x": 1}}})
        real_load = destroy.hosting.load_host_state
        load = lambda: real_load(root / "hosts.json")

        probe_output = (
            "containers=0\nvolumes=0\nnetworks=0\nimages=0\n"
            "retained_images=2\nretained_untagged=1\n"
            "runtime_kb=0\nsource_kb=0\nroute=0\ncerts=0\n"
            "retained_image=shared/base:1\nretained_image=shared/db:1\n"
        )
        commands = []

        def ssh(entry, command, timeout=30):
            commands.append(command)
            return subprocess.CompletedProcess([], 0, probe_output, "")

        client = FakeClient()
        with mock.patch.object(destroy.remote, "ssh_run", ssh), \
                mock.patch("sandbox.hosting.recovery.repository.RecoveryRepository",
                           lambda: repository), \
                mock.patch.object(destroy.hosting, "load_host_state", load):
            result = destroy.execute(ENTRY, "r1", "site", "sandbox", client_factory=lambda: client)

        self.assertEqual(result["state"], "absent")
        self.assertEqual(result["remote"]["retained_images"], 2)
        self.assertEqual(result["remote"]["retained_untagged"], 1)
        self.assertEqual(result["remote"]["retained_refs"], ["shared/base:1", "shared/db:1"])
        rendered = destroy.render(result, confirmed=False)
        self.assertEqual(
            rendered,
            "r1/site/sandbox: nothing to remove; kept 2 labelled images not proven ours: shared/base:1, shared/db:1, 1 untagged",
        )
        self.assertEqual(len(commands), 1)
        self.assertIn("docker ps", commands[0])
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()


class FencePlaceholders(unittest.TestCase):
    def test_placeholder_on_a_colliding_name_does_not_block_retry(self):
        fence = {"compose_project": "sandbox-host-site-blue-sandbox",
                 "active_operation": {"action": "destroy", "started_at": 1}}
        state = {"hosts": {"r1/site-blue/sandbox": dict(fence), "r1/site/blue-sandbox": dict(fence)}}
        self.assertEqual(destroy._collisions(state, "r1", "r1/site-blue/sandbox",
                                             "sandbox-host-site-blue-sandbox"), [])
        self.assertEqual(destroy.namesakes("r1", "site-blue", "sandbox"),
                         ["r1/site-blue/sandbox", "r1/site/blue-sandbox"])

    def test_retire_delivery_sees_a_destroy_owner(self):
        from sandbox.commands.hosting import _destroy_owns
        state = {"hosts": {"r1/a/b": {"active_operation": {"action": "destroy"}},
                           "r1/c/d": {"active_operation": {"action": "apply"}}}}
        self.assertTrue(_destroy_owns(state, "r1/a/b"))
        self.assertFalse(_destroy_owns(state, "r1/c/d"))
        self.assertFalse(_destroy_owns(state, "r1/x/y"))


class DestroyFenceAdmission(unittest.TestCase):
    def test_image_activation_refuses_while_destroy_owns_the_target(self):
        from sandbox.hosting.recovery.repository import RecoveryRepository, _ActivationHostStatePort
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = RecoveryRepository(root / "hosts.json", root / "locks")
            repository._write({"version": 1, "hosts": {
                KEY: {"active_operation": {"action": "destroy", "started_at": 1}},
                "r1/keep/sandbox": {}}})
            port = _ActivationHostStatePort(repository)
            with port.atomic_host_state_transaction(KEY):
                with self.assertRaisesRegex(ValueError, "operation_busy"):
                    port.read_activation_nested(KEY)
            with port.atomic_host_state_transaction("r1/keep/sandbox"):
                port.read_activation_nested("r1/keep/sandbox")
