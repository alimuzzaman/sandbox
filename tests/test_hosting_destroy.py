from sandbox.core import _hosting as hosting
from sandbox.hosting.recovery.repository import RecoveryRepository
import json
import contextlib
import base64
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



def guarded_destroy_case(method):
    """Deny every transport unless the individual fixture explicitly supplies it."""
    from functools import wraps
    from tests.zone_coverage_test_support import GuardedIO
    @wraps(method)
    def wrapped(self):
        guard = GuardedIO()
        guard.__enter__()
        # Leave through addCleanup: a patch the test registers with addCleanup saved
        # guard.reject as its original, so it has to unwind before the guard does.
        self.addCleanup(guard.__exit__, None, None, None)
        try:
            return method(self)
        finally:
            if guard.attempts:
                raise AssertionError("unexpected I/O attempts: %r" % (guard.attempts,))
    return wrapped


def destroy_dispatch(project, environment, probe, store=None, events=None, *, fail_teardown=False, on_edge=None):
    """Match commands generated for exactly one fixture target; never execute them."""
    name = destroy.compose_name(project, environment)
    # Execution class patches resolve_sandbox_home to this fixed synthetic path.
    home = "/home/u/sandbox"
    paths = destroy._paths(home, project, environment)
    route = destroy._route_file(name, "nginx")
    certs = destroy._cert_dirs(name, "nginx")
    expected_probe = destroy._probe_command(name, paths, route, certs)
    expected_teardown = destroy._remote_teardown_command(name, paths)
    capture = []
    def capture_command(entry, command, timeout=30):
        capture.append(command)
        return subprocess.CompletedProcess([], 0, "", "")
    with mock.patch.object(destroy.remote, "ssh_run", capture_command):
        destroy._remove_edge({}, name, "nginx", False)
    expected_edge, = capture
    phase = "probe"
    def dispatch(entry, command, timeout=30):
        nonlocal phase
        if command == expected_probe and phase == "probe":
            phase = "edge"
            return subprocess.CompletedProcess([], 0, probe, "")
        if command == expected_edge and phase == "edge":
            if on_edge is not None:
                on_edge(command)
            phase = "teardown"
            return subprocess.CompletedProcess([], 0, "", "")
        if command == expected_teardown and phase == "teardown":
            phase = "release"
            return subprocess.CompletedProcess([], 1 if fail_teardown else 0, "" if fail_teardown else "removed\n", "")
        from tests.zone_coverage_test_support import validate_store_command
        payload = validate_store_command(command)
        request = json.loads(base64.b64decode(payload, validate=True))
        if store is None or request.get("action") != "dependent-release":
            raise AssertionError("unexpected destroy command in " + phase)
        result = store(entry, command, timeout=timeout)
        if events is not None and result.returncode == 0 and not json.loads(result.stdout).get("refused"):
            events.append("store_release")
        return result
    return dispatch

class GuardedCaseUnwinding(unittest.TestCase):
    def test_patch_cleaned_up_after_a_guarded_case_does_not_restore_the_guard(self):
        real = destroy.remote.ssh_run

        class Case(unittest.TestCase):
            @guarded_destroy_case
            def runTest(self):
                stack = contextlib.ExitStack()
                self.addCleanup(stack.close)
                stack.enter_context(mock.patch.object(destroy.remote, "ssh_run", mock.Mock()))

        result = Case().run()
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertIs(destroy.remote.ssh_run, real)


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

        for variation in (
            {"compose_project": "sandbox-host-site-blue-sandbox", "active_operation": {"action": "apply"}},
            {"compose_project": "sandbox-host-site-blue-sandbox", "active_operation": {"action": "destroy"}, "records": []},
        ):
            with self.subTest(variation=variation):
                st = {"hosts": {"r1/site-blue/sandbox": dict(variation)}}
                with self.assertRaises(destroy.DestroyError) as caught:
                    destroy.plan(st, ENTRY, "r1", "site", "blue-sandbox", probe=dict(EMPTY_PROBE))
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


orig_load_host_state = hosting.load_host_state

@mock.patch.object(destroy.remote, "resolve_sandbox_home", lambda entry: "/home/u/sandbox")
class Execution(unittest.TestCase):
    def _run(self, record, ssh_outputs, client, extra_hosts=None,
             project="site", environment="sandbox", record_ssh=False, strict_dispatch=None, **kwargs):
        from sandbox.hosting.recovery.repository import RecoveryRepository
        self.events = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        repository = RecoveryRepository(root / "hosts.json", root / "locks")
        self.repository = repository
        target_key = f"r1/{project}/{environment}"
        hosts = {target_key: record, "r1/keep/sandbox": {"x": 1}}
        if extra_hosts:
            hosts.update(extra_hosts)
        repository._write({"version": 1, "hosts": hosts})
        self._last_state = lambda: real_load(root / "hosts.json")
        replies = iter(ssh_outputs)
        commands = []

        def ssh(entry, command, timeout=30):
            if record_ssh:
                self.events.append("ssh")
            commands.append(command)
            if strict_dispatch is not None:
                return strict_dispatch(entry, command, timeout=timeout)
            reply = next(replies)
            if callable(reply):
                reply = reply(repository)
            code, out = reply if isinstance(reply, tuple) else (0, reply)
            return subprocess.CompletedProcess([], code, out, "")

        real_state_lock = repository.state_lock

        @contextlib.contextmanager
        def tracking_state_lock(*args, **kw):
            self.events.append("lock")
            try:
                with real_state_lock(*args, **kw):
                    yield
            finally:
                self.events.append("unlock")

        real_write = repository._write

        def tracking_write(*args, **kw):
            self.events.append("write")
            return real_write(*args, **kw)

        repository.state_lock = tracking_state_lock
        repository._write = tracking_write

        real_load = destroy.hosting.load_host_state
        load = kwargs.pop("load_hook", None) or (lambda: real_load(root / "hosts.json"))
        client_factory = kwargs.pop("client_factory", lambda: client)
        with mock.patch.object(destroy.remote, "ssh_run", ssh), \
                mock.patch("sandbox.hosting.recovery.repository.RecoveryRepository",
                           lambda: repository), \
                mock.patch.object(destroy.hosting, "load_host_state", load):
            result = destroy.execute(ENTRY, "r1", project, environment,
                                     client_factory=client_factory, **kwargs)
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

    def shared_fixture(self, record=None, *, fail_teardown=False, fail_store=False, project="site", environment="sandbox", probe=None, on_edge=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        zone = "asb.bd" if project == "coverage" else "example.com"
        key = f"r1/{project}/{environment}"
        zones = root / "store" / "runtime" / "zone-coverage" / "zones"
        zones.mkdir(parents=True)
        shared = {"id": "rec-w", "name": "*." + zone, "type": "A", "content": "1.1.1.1", "proxied": True}
        doc = {"schema_version": 1, "zone": zone, "state": "active", "generation": 1,
               "dependents_revision": 1, "record": shared, "shared_records": [shared],
               "dependents": {key: {"hostname": "app." + zone, "state": "bound", "generation": 1}}, "history": []}
        zonefile = zones / (zone + ".json")
        zonefile.write_text(json.dumps(doc))
        if record is None:
            record = _record(records=[], dns_coverage={"mode": "shared", "zone": zone, "hostname": "app." + zone})
        repository = RecoveryRepository(root / "hosts.json", root / "locks")
        repository._write({"version": 1, "hosts": {key: record}})
        events = []
        original_write = repository._write
        def write(state):
            result = original_write(state)
            if key not in state.get("hosts", {}):
                events.append("state_removal")
            return result
        repository._write = write
        from tests.zone_coverage_test_support import store_transport
        store = store_transport(str(root / "store"))
        def guarded_store(entry, command, timeout=15):
            if fail_store:
                return subprocess.CompletedProcess([], 1, "", "synthetic failure")
            return store(entry, command, timeout=timeout)
        dispatch = destroy_dispatch(project, environment, probe or self.PROBE, guarded_store, events, fail_teardown=fail_teardown, on_edge=on_edge)
        stack = contextlib.ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(destroy.remote, "ssh_run", dispatch))
        stack.enter_context(mock.patch("sandbox.hosting.recovery.repository.RecoveryRepository", lambda: repository))
        stack.enter_context(mock.patch.object(destroy.hosting, "load_host_state", lambda: orig_load_host_state(root / "hosts.json")))
        class StatefulClient(FakeClient):
            def restore_record(self, zone_id, previous, created_id=None):
                super().restore_record(zone_id, previous, created_id)
                if created_id:
                    self.current.pop(created_id, None)
                if previous:
                    self.current[previous["id"]] = dict(previous)
        client = StatefulClient()
        client.current["rec-w"] = dict(shared)
        return root, key, zonefile, shared, repository, events, client

    @guarded_destroy_case
    def test_shared_destroy_releases_after_state_removal_real_store(self):
        root, key, zonefile, shared, repository, events, client = self.shared_fixture()
        state = orig_load_host_state(root / "hosts.json")
        planned = destroy.plan(state, ENTRY, "r1", "site", "sandbox", probe=dict(EMPTY_PROBE))
        self.assertEqual(planned["shared_coverage_retained"], [{"hostname": "app.example.com", "zone": "example.com"}])
        self.assertIn("shared zone coverage retained for app.example.com in example.com", destroy.render(planned, confirmed=False))
        result = destroy.execute(ENTRY, "r1", "site", "sandbox", client_factory=lambda: client)
        self.assertEqual(result["state"], "destroyed")
        self.assertIs(result["shared_coverage_retained"], True)
        self.assertEqual(result["coverage_release"], "released")
        self.assertIn("shared zone coverage retained for app.example.com", destroy.render(result, confirmed=True))
        self.assertLess(events.index("state_removal"), events.index("store_release"))
        self.assertNotIn(key, json.loads(zonefile.read_text())["dependents"])
        self.assertNotIn(key, repository.load()["hosts"])

    @guarded_destroy_case
    def test_shared_destroy_release_failure_is_reported(self):
        root, key, zonefile, shared, repository, events, client = self.shared_fixture(fail_store=True)
        result = destroy.execute(ENTRY, "r1", "site", "sandbox", client_factory=lambda: client)
        self.assertEqual(result["state"], "destroyed")
        self.assertIs(result["shared_coverage_retained"], True)
        self.assertEqual(result["coverage_release"], "unknown")
        self.assertNotIn(key, repository.load()["hosts"])
        self.assertIn(key, json.loads(zonefile.read_text())["dependents"])
        self.assertIn(key, repository.load()["coverage_release_pending"])

    @guarded_destroy_case
    def test_absent_destroy_retries_legacy_pending_coverage_release(self):
        root, key, zonefile, shared, repository, events, client = self.shared_fixture()
        repository._write({"version": 1, "hosts": {},
                           "coverage_release_pending": {key: ["example.com"]}})
        result = destroy.execute(ENTRY, "r1", "site", "sandbox", client_factory=lambda: client)
        self.assertIn(result["state"], {"absent", "destroyed"})
        self.assertNotIn(key, json.loads(zonefile.read_text())["dependents"])
        self.assertNotIn(key, repository.load().get("coverage_release_pending", {}))

    @guarded_destroy_case
    def test_fenced_or_failed_destroy_keeps_dependent(self):
        for kind in ("fence", "teardown", "provider", "save"):
            with self.subTest(kind=kind):
                record = _record(records=[], dns_coverage={"mode": "shared", "zone": "example.com", "hostname": "app.example.com"})
                if kind == "fence": record["active_operation"] = {"action": "apply", "started_at": 1}
                root, key, zonefile, shared, repository, events, client = self.shared_fixture(record, fail_teardown=kind == "teardown")
                if kind == "provider": client.revoke_origin_certificate = mock.Mock(side_effect=cloudflare.CloudflareError("synthetic"))
                if kind == "save":
                    write = repository._write
                    def fail_removal(state):
                        if key not in state.get("hosts", {}): raise OSError("synthetic save failure")
                        return write(state)
                    repository._write = fail_removal
                with self.assertRaises((destroy.DestroyError, cloudflare.CloudflareError, OSError)):
                    destroy.execute(ENTRY, "r1", "site", "sandbox", client_factory=lambda: client)
                self.assertEqual(events.count("store_release"), 0)
                self.assertIn(key, json.loads(zonefile.read_text())["dependents"])
                self.assertIn(key, repository.load()["hosts"])

    @guarded_destroy_case
    def test_coverage_name_collision_never_removes_fallback_or_certificate(self):
        record = _record(compose_project="sandbox-host-coverage-asb.bd", records=[],
                         dns_coverage={"mode": "shared", "zone": "asb.bd", "hostname": "app.asb.bd"})
        probe = self.PROBE.replace("route=0", "route=1").replace("certs=0", "certs=1")
        resources = {}
        def remove_target_cert(command):
            resources["target_cert"].unlink()
        root, key, zonefile, shared, repository, events, client = self.shared_fixture(record, project="coverage", environment="asb.bd", probe=probe, on_edge=remove_target_cert)
        fallback = root / "fallback.conf"; fallback.write_bytes(b"fallback bytes")
        cert = root / "fallback.pem"; cert.write_bytes(b"fallback cert bytes")
        target = root / "target.conf"; target.write_bytes(b"target route")
        resources["target_cert"] = root / "target-cert.pem"; resources["target_cert"].write_bytes(b"target cert")
        from sandbox.hosting.front_door import nginx
        expected = nginx.transaction_command(nginx.route_file("host-coverage-asb.bd"), None, "absent", operation="apply", remove=True)
        def nginx_ssh(entry, command, timeout=30):
            self.assertEqual(command, expected)
            target.unlink()
            return subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(nginx, "_ssh", nginx_ssh):
            result = destroy.execute(ENTRY, "r1", "coverage", "asb.bd", client_factory=lambda: client)
        self.assertEqual(result["coverage_release"], "released")
        self.assertFalse(target.exists())
        self.assertFalse(resources["target_cert"].exists())
        self.assertEqual(client.current["rec-w"], shared)
        self.assertEqual(fallback.read_bytes(), b"fallback bytes")
        self.assertEqual(cert.read_bytes(), b"fallback cert bytes")
        self.assertIn(("cert", "cert1"), client.calls)
        self.assertEqual(json.loads(zonefile.read_text())["shared_records"], [shared])

    @guarded_destroy_case
    def test_shared_destroy_with_leftover_owned_records(self):
        snapshot = {"name": "site.example.com", "type": "A", "content": "1.2.3.4", "proxied": True, "ttl": None, "comment": None}
        wildcard = {"name": "*.example.com", "type": "A", "content": "1.1.1.1", "proxied": True}
        record = _record(records=[{"zone_id": "z1", "created_id": "rec-leftover", "applied": snapshot},
                                  {"zone_id": "z1", "created_id": "rec-w", "applied": wildcard}],
                         dns_coverage={"mode": "shared", "zone": "example.com", "hostname": "site.example.com"})
        root, key, zonefile, shared, repository, events, client = self.shared_fixture(record)
        client.current["rec-leftover"] = dict(snapshot, id="rec-leftover")
        planned = destroy.plan(orig_load_host_state(root / "hosts.json"), ENTRY, "r1", "site", "sandbox", probe=dict(EMPTY_PROBE))
        self.assertEqual(planned["dns_records"][0]["record_id"], "rec-leftover")
        self.assertEqual(planned["dns_records"][0]["action"], "delete")
        result = destroy.execute(ENTRY, "r1", "site", "sandbox", client_factory=lambda: client)
        self.assertEqual(result["state"], "destroyed")
        self.assertIn(("dns", "z1", "rec-leftover"), client.calls)
        self.assertNotIn("rec-leftover", client.current)
        self.assertEqual(client.current["rec-w"], shared)
        self.assertEqual(json.loads(zonefile.read_text())["shared_records"], [shared])
        self.assertNotIn(key, json.loads(zonefile.read_text())["dependents"])

    @guarded_destroy_case
    def test_pending_coverage_release_claim_is_released_after_destroy(self):
        record = _record(records=[], coverage_release_pending={"mode": "shared", "zone": "example.com"})
        root, key, zonefile, shared, repository, events, client = self.shared_fixture(record)
        result = destroy.execute(ENTRY, "r1", "site", "sandbox", client_factory=lambda: client)
        self.assertEqual(result["coverage_release"], "released")
        self.assertLess(events.index("state_removal"), events.index("store_release"))
        self.assertNotIn(key, json.loads(zonefile.read_text())["dependents"])

    @guarded_destroy_case
    def test_destroy_releases_all_current_and_pending_zone_claims(self):
        record = _record(records=[], dns_coverage={"mode": "shared", "zone": "other.test", "hostname": "app.other.test"},
                         coverage_release_pending=[{"mode": "shared", "zone": "example.com"},
                                                   {"mode": "shared", "zone": "other.test"}])
        root, key, zonefile, shared, repository, events, client = self.shared_fixture(record)
        otherfile = zonefile.parent / "other.test.json"
        doc = json.loads(zonefile.read_text())
        doc["zone"] = "other.test"
        doc["record"] = {**doc["record"], "name": "*.other.test"}
        doc["shared_records"] = [doc["record"]]
        doc["dependents"][key]["hostname"] = "app.other.test"
        otherfile.write_text(json.dumps(doc))
        plan = destroy.plan(repository.load(), ENTRY, "r1", "site", "sandbox", probe=dict(EMPTY_PROBE))
        self.assertEqual({row["zone"] for row in plan["shared_coverage_retained"]}, {"example.com", "other.test"})
        result = destroy.execute(ENTRY, "r1", "site", "sandbox", client_factory=lambda: client)
        self.assertEqual(result["coverage_release"], "released")
        self.assertNotIn(key, json.loads(zonefile.read_text())["dependents"])
        self.assertNotIn(key, json.loads(otherfile.read_text())["dependents"])
        self.assertEqual(events.count("store_release"), 2)
        self.assertLess(events.index("state_removal"), events.index("store_release"))

    @guarded_destroy_case
    def test_destroy_retains_only_failed_zone_claim(self):
        record = _record(records=[], dns_coverage={"mode": "shared", "zone": "other.test"},
                         coverage_release_pending={"mode": "shared", "zone": "example.com"})
        root, key, zonefile, shared, repository, events, client = self.shared_fixture(record)
        otherfile = zonefile.parent / "other.test.json"
        doc = json.loads(zonefile.read_text()); doc["zone"] = "other.test"
        doc["record"] = {**doc["record"], "name": "*.other.test"}; doc["shared_records"] = [doc["record"]]
        otherfile.write_text(json.dumps(doc))
        previous = destroy.remote.ssh_run
        def one_failure(entry, command, timeout=15):
            from tests.zone_coverage_test_support import validate_store_command
            try:
                request = json.loads(base64.b64decode(validate_store_command(command), validate=True))
            except AssertionError:
                return previous(entry, command, timeout=timeout)
            if request.get("action") == "dependent-release" and request.get("zone") == "other.test":
                return subprocess.CompletedProcess([], 1, "", "synthetic failure")
            return previous(entry, command, timeout=timeout)
        with mock.patch.object(destroy.remote, "ssh_run", side_effect=one_failure):
            result = destroy.execute(ENTRY, "r1", "site", "sandbox", client_factory=lambda: client)
        self.assertEqual(result["coverage_release"], "unknown")
        self.assertIn(key, json.loads(otherfile.read_text())["dependents"])
        self.assertNotIn(key, json.loads(zonefile.read_text())["dependents"])
        self.assertEqual(repository.load()["coverage_release_pending"][key], ["other.test"])

    @guarded_destroy_case
    def test_pending_coverage_claim_survives_failed_teardown(self):
        record = _record(records=[], coverage_release_pending={"mode": "shared", "zone": "example.com"})
        root, key, zonefile, shared, repository, events, client = self.shared_fixture(record, fail_teardown=True)
        with self.assertRaises(destroy.DestroyError):
            destroy.execute(ENTRY, "r1", "site", "sandbox", client_factory=lambda: client)
        self.assertEqual(events.count("store_release"), 0)
        self.assertIn(key, json.loads(zonefile.read_text())["dependents"])
        self.assertEqual(repository.load()["hosts"][key]["coverage_release_pending"]["zone"], "example.com")

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

    def test_record_adopted_during_teardown_is_kept(self):
        applied = {
            "name": "a.test", "type": "A", "content": "192.0.2.1",
            "proxied": True, "ttl": 1, "comment": "managed by Sandbox hosting",
        }
        client = FakeClient()
        client.current = {"x": {"id": "x", **applied}}
        record = _record(records=[{
            "zone_id": "z1", "created_id": "x", "previous": None,
            "applied": dict(applied),
        }])
        record_calls = []
        orig_record = client.record
        client.record = lambda zone_id, record_id: record_calls.append((zone_id, record_id)) or orig_record(zone_id, record_id)

        def teardown_reply(repo):
            st = self._last_state()
            st.setdefault("hosts", {})["r1/other/sandbox"] = {
                "records": [{"zone_id": "z1", "created_id": "x", "previous": None}],
            }
            repo._write(st)
            return "removed\n"

        result, _, state = self._run(record, [self.PROBE, "", teardown_reply], client)
        self.assertEqual(result["state"], "destroyed")
        self.assertIn({"zone_id": "z1", "state": "kept_shared", "record_id": "x"},
                      result["cloudflare"])
        dns_calls = [call for call in client.calls if call[0] == "dns"]
        self.assertEqual(dns_calls, [])
        self.assertEqual(record_calls, [])
        self.assertNotIn(KEY, state["hosts"])
        self.assertIn("r1/other/sandbox", state["hosts"])
        self.assertEqual(state["hosts"]["r1/other/sandbox"]["records"],
                         [{"zone_id": "z1", "created_id": "x", "previous": None}])
        rendered = destroy.render(result, confirmed=True)
        self.assertIn("left in place", rendered)
        self.assertIn("is now held by another target", rendered)

    def test_previous_only_row_adopted_during_teardown_is_kept(self):
        client = FakeClient()
        record = _record(records=[{
            "zone_id": "z1", "created_id": None, "previous": {"id": "x"},
        }])
        record_calls = []
        orig_record = client.record
        client.record = lambda zone_id, record_id: record_calls.append((zone_id, record_id)) or orig_record(zone_id, record_id)

        def teardown_reply(repo):
            st = self._last_state()
            st.setdefault("hosts", {})["r1/other/sandbox"] = {
                "records": [{"zone_id": "z1", "created_id": "x", "previous": None}],
            }
            repo._write(st)
            return "removed\n"

        result, _, state = self._run(record, [self.PROBE, "", teardown_reply], client)
        self.assertEqual(result["state"], "destroyed")
        self.assertIn({"zone_id": "z1", "state": "kept_shared", "record_id": "x"},
                      result["cloudflare"])
        dns_calls = [call for call in client.calls if call[0] == "dns"]
        self.assertEqual(dns_calls, [])
        self.assertEqual(record_calls, [])
        self.assertNotIn(KEY, state["hosts"])
        self.assertIn("r1/other/sandbox", state["hosts"])
        rendered = destroy.render(result, confirmed=True)
        self.assertIn("left in place", rendered)

    def test_provider_step_and_state_removal_share_one_lock(self):
        test_self = self

        class TrackingClient(FakeClient):
            def record(self, zone_id, record_id):
                test_self.events.append("record")
                return super().record(zone_id, record_id)

            def restore_record(self, zone_id, previous, created_id=None):
                test_self.events.append("restore")
                return super().restore_record(zone_id, previous, created_id)

        applied = {
            "name": "a.test", "type": "A", "content": "192.0.2.1",
            "proxied": True, "ttl": 1, "comment": "managed by Sandbox hosting",
        }
        client = TrackingClient()
        client.current = {"rec1": {"id": "rec1", **applied}}
        record = _record(records=[{
            "zone_id": "z1", "created_id": "rec1", "previous": None,
            "applied": dict(applied),
        }], certificate=None)

        result, _, _ = self._run(
            record, [self.PROBE, "", "removed\n"], client, record_ssh=True)
        self.assertEqual(result["state"], "destroyed")
        self.assertEqual(self.events[-5:],
                         ["lock", "record", "restore", "write", "unlock"])

        in_lock = False
        for event in self.events:
            if event == "lock":
                self.assertFalse(in_lock)
                in_lock = True
            elif event == "unlock":
                self.assertTrue(in_lock)
                in_lock = False
            elif event == "ssh":
                self.assertFalse(in_lock, "ssh called while state lock held")

    def test_no_cloudflare_work_needs_no_token(self):
        record = _record(records=[], certificate=None)

        def raising_factory():
            raise cloudflare.CloudflareError("Cloudflare is not connected")

        result, _, _ = self._run(
            record, [self.PROBE, "", "removed\n"], None, client_factory=raising_factory)
        self.assertEqual(result["state"], "destroyed")
        self.assertEqual(result["cloudflare"], [])

    def test_missing_token_fails_before_the_lock_and_keeps_the_fence(self):
        def raising_factory():
            raise cloudflare.CloudflareError("Cloudflare is not connected")

        with self.assertRaises(cloudflare.CloudflareError):
            self._run(_record(), [self.PROBE, "", "removed\n"], None, client_factory=raising_factory)
        state = self._last_state()
        self.assertIn(KEY, state["hosts"])
        self.assertEqual(state["hosts"][KEY]["active_operation"]["action"], "destroy")
        self.assertEqual(self.events[-1], "unlock")

    def test_row_dropped_before_teardown_is_not_added_back(self):
        record = _record(records=[{
            "zone_id": "z1", "created_id": "x", "previous": None,
        }], certificate=None)
        extra_hosts = {
            "r1/other/sandbox": {
                "records": [{"zone_id": "z1", "created_id": "x", "previous": None}],
            }
        }

        def teardown_reply(repo):
            st = self._last_state()
            st.setdefault("hosts", {}).pop("r1/other/sandbox", None)
            repo._write(st)
            return "removed\n"

        def raising_factory():
            raise AssertionError("client factory must not be called")

        result, _, state = self._run(
            record, [self.PROBE, "", teardown_reply], None,
            extra_hosts=extra_hosts, client_factory=raising_factory)
        self.assertEqual(result["state"], "destroyed")
        self.assertNotIn(KEY, state["hosts"])

    def test_lock_timeout_after_teardown_keeps_state_and_retry_works(self):
        from sandbox.hosting.recovery.repository import RecoveryRepository
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = RecoveryRepository(root / "hosts.json", root / "locks")
            client = FakeClient()
            record = _record()
            repository._write({"version": 1, "hosts": {KEY: record, "r1/keep/sandbox": {"x": 1}}})

            replies = iter([self.PROBE, "", "removed\n", self.PROBE, "", "removed\n"])

            def ssh(entry, command, timeout=30):
                reply = next(replies)
                code, out = reply if isinstance(reply, tuple) else (0, reply)
                return subprocess.CompletedProcess([], code, out, "")

            real_lock = repository.state_lock
            lock_count = 0
            fail_third_lock = True

            @contextlib.contextmanager
            def conditional_state_lock(*args, **kwargs):
                nonlocal lock_count
                lock_count += 1
                if fail_third_lock and lock_count == 3:
                    raise TimeoutError("operation_busy")
                with real_lock(*args, **kwargs):
                    yield

            repository.state_lock = conditional_state_lock
            real_load = destroy.hosting.load_host_state
            load = lambda: real_load(root / "hosts.json")

            with mock.patch.object(destroy.remote, "ssh_run", ssh), \
                    mock.patch("sandbox.hosting.recovery.repository.RecoveryRepository",
                               lambda: repository), \
                    mock.patch.object(destroy.hosting, "load_host_state", load):
                with self.assertRaises(destroy.DestroyError) as ctx:
                    destroy.execute(ENTRY, "r1", "site", "sandbox", client_factory=lambda: client)
                self.assertEqual(ctx.exception.code, "state_lock_timeout")
                self.assertEqual(client.calls, [])
                state = load()
                self.assertIn(KEY, state["hosts"])
                self.assertEqual(state["hosts"][KEY]["active_operation"]["action"], "destroy")

                fail_third_lock = False
                result = destroy.execute(ENTRY, "r1", "site", "sandbox",
                                         client_factory=lambda: client, clear_fenced=True)
                self.assertEqual(result["state"], "destroyed")
                end_state = load()
                self.assertNotIn(KEY, end_state["hosts"])
                self.assertIn("r1/keep/sandbox", end_state["hosts"])

    def test_a_local_timeout_is_not_blamed_on_cloudflare(self):
        real = destroy._records_held_elsewhere
        calls = []

        def held(state, key):
            calls.append(key)
            # plan and the first read in execute are 1 and 2; 3 is under the final lock.
            if len(calls) == 3:
                raise TimeoutError("local")
            return real(state, key)

        client = FakeClient()
        with mock.patch.object(destroy, "_records_held_elsewhere", held), \
                self.assertRaises(destroy.DestroyError) as caught:
            self._run(_record(), [self.PROBE, "", "removed\n"], client)
        self.assertEqual(caught.exception.code, "state_timeout")
        self.assertEqual(client.calls, [])

    def test_provider_read_timeout_is_not_called_a_busy_lock(self):
        applied = {
            "name": "a.test", "type": "A", "content": "192.0.2.1",
            "proxied": True, "ttl": 1, "comment": "managed by Sandbox hosting",
        }
        client = FakeClient()

        def raising_record(zone_id, record_id):
            raise TimeoutError("read timed out")

        client.record = raising_record
        record = _record(records=[{
            "zone_id": "z1", "created_id": "rec1", "previous": None,
            "applied": dict(applied),
        }])

        with self.assertRaises(destroy.DestroyError) as ctx:
            self._run(record, [self.PROBE, "", "removed\n"], client)
        self.assertEqual(ctx.exception.code, "provider_timeout")
        state = self._last_state()
        self.assertIn(KEY, state["hosts"])
        self.assertEqual(state["hosts"][KEY]["active_operation"]["action"], "destroy")

    def test_provider_failure_midway_keeps_state_and_retry_finishes(self):
        from sandbox.hosting.recovery.repository import RecoveryRepository

        class FlakyClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.y_failed = False

            def restore_record(self, zone_id, previous, created_id=None):
                super().restore_record(zone_id, previous, created_id=created_id)
                if created_id == "y" and not self.y_failed:
                    self.y_failed = True
                    raise cloudflare.CloudflareError("Cloudflare request failed: HTTP 500: internal error")
                self.current.pop(created_id, None)

        applied = {
            "name": "a.test", "type": "A", "content": "192.0.2.1",
            "proxied": True, "ttl": 1, "comment": "managed by Sandbox hosting",
        }
        client = FlakyClient()
        client.current = {
            "x": {"id": "x", **applied},
            "y": {"id": "y", **applied},
        }
        record = _record(records=[
            {"zone_id": "z1", "created_id": "x", "previous": None, "applied": dict(applied)},
            {"zone_id": "z1", "created_id": "y", "previous": None, "applied": dict(applied)},
        ], certificate=None)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = RecoveryRepository(root / "hosts.json", root / "locks")
            repository._write({"version": 1, "hosts": {KEY: record, "r1/keep/sandbox": {"x": 1}}})

            replies = iter([self.PROBE, "", "removed\n", self.PROBE, "", "removed\n"])

            def ssh(entry, command, timeout=30):
                reply = next(replies)
                code, out = reply if isinstance(reply, tuple) else (0, reply)
                return subprocess.CompletedProcess([], code, out, "")

            real_load = destroy.hosting.load_host_state
            load = lambda: real_load(root / "hosts.json")

            with mock.patch.object(destroy.remote, "ssh_run", ssh), \
                    mock.patch("sandbox.hosting.recovery.repository.RecoveryRepository",
                               lambda: repository), \
                    mock.patch.object(destroy.hosting, "load_host_state", load):
                with self.assertRaises(cloudflare.CloudflareError):
                    destroy.execute(ENTRY, "r1", "site", "sandbox", client_factory=lambda: client)
                self.assertIn(("dns", "z1", "x"), client.calls)
                self.assertNotIn("x", client.current)
                state = load()
                self.assertIn(KEY, state["hosts"])
                self.assertEqual(state["hosts"][KEY]["active_operation"]["action"], "destroy")

                result = destroy.execute(ENTRY, "r1", "site", "sandbox",
                                         client_factory=lambda: client, clear_fenced=True)
                self.assertEqual(result["state"], "destroyed")
                self.assertEqual(result["cloudflare"], [
                    {"zone_id": "z1", "state": "already_absent", "record_id": "x"},
                    {"zone_id": "z1", "state": "removed"},
                ])
                end_state = load()
                self.assertNotIn(KEY, end_state["hosts"])
                self.assertIn("r1/keep/sandbox", end_state["hosts"])

    def test_interrupted_destroy_fences_the_real_namesake_pair(self):
        from sandbox.commands import hosting as cmd_hosting
        from sandbox.hosting.recovery.repository import _ActivationHostStatePort

        record = _record(compose_project="sandbox-host-site-blue-sandbox")
        client = FakeClient()
        with self.assertRaises(destroy.DestroyError):
            self._run(record, [self.PROBE, "", (124, "")], client,
                      project="site-blue", environment="sandbox")

        state = self._last_state()
        port = _ActivationHostStatePort(self.repository)

        for key in ("r1/site-blue/sandbox", "r1/site/blue-sandbox"):
            self.assertIn(key, state["hosts"])
            self.assertEqual(state["hosts"][key]["active_operation"]["action"], "destroy")
            with self.assertRaises(destroy.hosting.HostingError):
                cmd_hosting._assert_no_active_host_operation(state, key)
            with port.atomic_host_state_transaction(key):
                with self.assertRaisesRegex(ValueError, "operation_busy"):
                    port.read_activation_nested(key)

        with port.atomic_host_state_transaction("r1/keep/sandbox"):
            port.read_activation_nested("r1/keep/sandbox")

    def test_retry_with_clear_fenced_removes_the_real_namesake_pair(self):
        fence = {"compose_project": "sandbox-host-site-blue-sandbox",
                 "active_operation": {"action": "destroy", "started_at": 1}}
        record = dict(fence)
        extra = {"r1/site/blue-sandbox": dict(fence)}
        result, _, state = self._run(
            record, [self.PROBE, "", "removed\n"], FakeClient(),
            extra_hosts=extra, project="site-blue", environment="sandbox",
            clear_fenced=True)
        self.assertEqual(result["state"], "destroyed")
        for key in ("r1/site-blue/sandbox", "r1/site/blue-sandbox"):
            self.assertNotIn(key, state["hosts"])
        self.assertIn("r1/keep/sandbox", state["hosts"])

    def test_fence_write_reloads_state_under_the_lock(self):
        record_retired = _record(retired_at=123)
        record_cleared = _record()
        self.assertNotIn("retired_at", record_cleared)

        loads = [
            {"version": 1, "hosts": {KEY: record_retired, "r1/keep/sandbox": {"x": 1}}},
            {"version": 1, "hosts": {KEY: record_cleared, "r1/keep/sandbox": {"x": 1}}},
        ]

        def fake_load():
            self.events.append("load")
            if loads:
                return loads.pop(0)
            return self._last_state()

        client = FakeClient()
        with self.assertRaises(destroy.DestroyError):
            self._run(_record(), [self.PROBE, "", (124, "")], client, load_hook=fake_load)

        written_state = self._last_state()
        written_record = written_state["hosts"][KEY]
        self.assertNotIn("retired_at", written_record)
        self.assertEqual(written_record["active_operation"]["action"], "destroy")

        load_indices = [i for i, ev in enumerate(self.events) if ev == "load"]
        self.assertEqual(len(load_indices), 2)
        second_load_idx = load_indices[1]
        self.assertEqual(self.events[second_load_idx - 1], "lock")
        self.assertEqual(self.events[second_load_idx + 1], "write")
        self.assertEqual(self.events[second_load_idx + 2], "unlock")

    def test_state_write_timeout_does_not_promise_the_fence_is_kept(self):
        from sandbox.hosting.recovery.repository import RecoveryRepository
        writes = []
        real_write = RecoveryRepository._write

        def counting_write(repo_self, state):
            if len(writes) == 2:
                raise TimeoutError("local state write timeout")
            writes.append(state)
            return real_write(repo_self, state)

        client = FakeClient()
        with mock.patch.object(RecoveryRepository, "_write", counting_write):
            with self.assertRaises(destroy.DestroyError) as caught:
                self._run(_record(), [self.PROBE, "", "removed\n"], client)

        self.assertEqual(len(writes), 2)
        self.assertEqual(caught.exception.code, "state_timeout")
        self.assertIn("check it with a dry run", str(caught.exception))
        self.assertNotIn("state and fence kept", str(caught.exception))

    def test_keeps_a_newly_shared_record_but_still_revokes_this_targets_certificate(self):
        client = FakeClient()
        record = _record(records=[{
            "zone_id": "z1", "created_id": "x", "previous": None,
        }])

        def teardown_reply(repo):
            st = self._last_state()
            st.setdefault("hosts", {})["r1/other/sandbox"] = {
                "records": [{"zone_id": "z1", "created_id": "x", "previous": None}],
                "certificate": {"id": "cert2", "hostnames": ["other.example"]},
            }
            repo._write(st)
            return "removed\n"

        result, _, _ = self._run(record, [self.PROBE, "", teardown_reply], client)
        self.assertEqual(result["state"], "destroyed")
        cloud = result["cloudflare"]
        self.assertIn({"zone_id": "z1", "state": "kept_shared", "record_id": "x"}, cloud)
        self.assertIn(("cert", "cert1"), client.calls)
        self.assertNotIn(("cert", "cert2"), client.calls)
        dns_calls = [call for call in client.calls if call[0] == "dns"]
        self.assertEqual(dns_calls, [])


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

    def test_recovery_on_a_destroy_placeholder_refuses_without_effects(self):
        from sandbox.hosting.recovery.models import (
            RecoveryAction, RecoveryRequest, TargetIdentity,
        )
        from sandbox.hosting.recovery.repository import RecoveryRepository
        from sandbox.hosting.recovery.service import RecoveryService

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = RecoveryRepository(root / "hosts.json", root / "locks")
            target = TargetIdentity("r1", "site", "sandbox")
            placeholder = {
                "compose_project": "sandbox-host-site-sandbox",
                "active_operation": {"action": "destroy", "started_at": 1},
            }
            repository._write({"version": 1, "hosts": {target.key: placeholder}})
            before_bytes = (root / "hosts.json").read_bytes()

            recorded_job_lookup = []
            recorded_observer = []
            recorded_edge_adapter = []

            def fake_job_lookup(job_id):
                recorded_job_lookup.append(job_id)
                return {}

            def fake_observer(*args, **kwargs):
                recorded_observer.append((args, kwargs))
                return {}

            def fake_edge_adapter(*args, **kwargs):
                recorded_edge_adapter.append((args, kwargs))
                return {}

            service = RecoveryService(
                repository=repository,
                job_lookup=fake_job_lookup,
                source_check=lambda *args: True,
                observer=fake_observer,
                edge_adapter=fake_edge_adapter,
            )
            request = RecoveryRequest(
                RecoveryAction.OBSERVE_RECONCILE, "recover-1", "job-1",
                "apply-1", target, 0,
            )
            result = service.recover(request)
            # The result family is 'failed' and code is 'persistence_failed'.
            # Note: the code is misleading, and this test pins refusal, not the wording.
            self.assertEqual(result["result_family"], "failed")
            self.assertEqual(result["result_class"], "persistence_failed")
            self.assertEqual(recorded_job_lookup, [])
            self.assertEqual(recorded_observer, [])
            self.assertEqual(recorded_edge_adapter, [])
            self.assertEqual((root / "hosts.json").read_bytes(), before_bytes)

    def test_activation_snapshot_refuses_under_a_destroy_fence(self):
        from sandbox.hosting.images.activation.repository import ActivationRepository
        from sandbox.hosting.recovery.repository import RecoveryRepository, _ActivationHostStatePort

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = RecoveryRepository(root / "hosts.json", root / "locks")
            repository._write({"version": 1, "hosts": {
                KEY: {"active_operation": {"action": "destroy", "started_at": 1}}}})
            before_bytes = (root / "hosts.json").read_bytes()

            activation_repo = ActivationRepository(
                host_state_port=_ActivationHostStatePort(repository),
                stage_repository=mock.Mock(),
                target_mutation_port=repository.target_mutation_port("activate"),
            )
            with self.assertRaisesRegex(ValueError, "operation_busy"):
                activation_repo.snapshot(KEY)
            self.assertEqual((root / "hosts.json").read_bytes(), before_bytes)
