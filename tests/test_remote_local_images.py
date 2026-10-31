"""Focused fixed-transport regressions for hosted local images."""
from __future__ import annotations

import io
import json
import shlex
import subprocess
import sys
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from sandbox.hosting import local_images
from sandbox.commands import hosting as hosting_cmd
from sandbox.transports import remote_local_images
from tests.test_hosting_local_images import _receipt
from tests.subprocess_support import run_test_process, synthetic_environment


class RemoteLocalImageTests(unittest.TestCase):
    def test_publish_accepts_byte_and_text_acknowledgements(self):
        document = {"synthetic": "document"}
        want = local_images.canonical_digest(document)
        frame = json.dumps({"schema": 1, "remote_receipt_digest": want})
        for output in (frame.encode("utf-8"), frame):
            with self.subTest(output_type=type(output).__name__), \
                 patch.object(remote_local_images._remote, "resolve_sandbox_home",
                              return_value="/srv/sandbox"), \
                 patch.object(remote_local_images._remote, "ssh_run", return_value=SimpleNamespace(
                     returncode=0, stdout=output)) as ssh:
                self.assertEqual(remote_local_images.publish("target", document, entry={}), want)
                self.assertIsInstance(ssh.call_args.kwargs["input_data"], bytes)
                self.assertEqual(json.loads(ssh.call_args.kwargs["input_data"]),
                                 {**document, "remote_receipt_digest": want})

    def test_publish_bad_acknowledgements_remain_unknown_without_echo(self):
        for output in (b"\xff", b"not-json", b"{}", b"[]", b"",
                       b"x" * (local_images.MAX_CONFIG_BYTES + 1),
                       "x" * (local_images.MAX_CONFIG_BYTES + 1)):
            with self.subTest(output_type=type(output).__name__, size=len(output)), \
                 patch.object(remote_local_images._remote, "resolve_sandbox_home",
                              return_value="/srv/sandbox"), \
                 patch.object(remote_local_images._remote, "ssh_run", return_value=SimpleNamespace(
                     returncode=0, stdout=output)):
                with self.assertRaises(remote_local_images.RemoteLocalImageError) as raised:
                    remote_local_images.publish("target", {"synthetic": "document"}, entry={})
                self.assertEqual(raised.exception.code, "local_images_receipt_publish_unknown")
                self.assertEqual(str(raised.exception),
                    "local_images_receipt_publish_unknown: local image transport failed")

    def test_push_cli_unexpected_exceptions_emit_unknown_json_without_details(self):
        args = SimpleNamespace(confirm=True, remote="target", receipt="/synthetic/receipt.json",
                               timeout=60, artifact_timeout=30, json=True)
        failures = (AttributeError("private-input"), ValueError("private-input"),
                    RuntimeError("private-input"), OSError("private-input"),
                    remote_local_images.RemoteLocalImageError("local_images_receipt_publish_unknown"))
        for failure in failures:
            with self.subTest(exception=type(failure).__name__), ExitStack() as stack:
                stack.enter_context(patch.object(hosting_cmd.remote, "registered_remote_lock"))
                stack.enter_context(patch.object(hosting_cmd.remote, "registration_lock_name",
                                                return_value="synthetic-lock"))
                stack.enter_context(patch.object(remote_local_images, "_entry", return_value={}))
                stack.enter_context(patch("sandbox.readiness.gate.require_ready"))
                stack.enter_context(patch.object(hosting_cmd.remote, "resolve_sandbox_home",
                                                return_value="/srv/sandbox"))
                stack.enter_context(patch.object(hosting_cmd, "_authenticated_machine_identity",
                                                return_value="synthetic-machine"))
                stack.enter_context(patch("sandbox.delivery.context.registered_host_digest",
                                         return_value="synthetic-host"))
                stack.enter_context(patch.object(remote_local_images, "push_receipt",
                                                side_effect=failure))
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr), \
                     self.assertRaises(SystemExit) as raised:
                    hosting_cmd._cmd_host_image_push_local(args)
                self.assertEqual(raised.exception.code, 1)
                payload = json.loads(stdout.getvalue())
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["code"], getattr(
                    failure, "code", "local_images_transfer_unknown"))
                self.assertNotIn("private-input", stdout.getvalue() + stderr.getvalue())
                self.assertEqual(stderr.getvalue(), "")

    @staticmethod
    def _write_receipt(directory: str) -> Path:
        path = Path(directory, "receipt.json")
        path.write_text(json.dumps(_receipt()))
        path.chmod(0o600)
        return path

    @staticmethod
    def _matching_observation() -> dict:
        return {"schema": 1, "engine": "linux/amd64", "services": {},
                "images": {"app:test": {"present": True,
                    "id": "sha256:" + "1" * 64, "platform": "linux/amd64",
                    "layers_digest": "sha256:" + "4" * 64,
                    "repo_digests": []}}, "config_error": None,
                "remote_receipt": None}

    def test_observe_uses_registered_remote_and_closed_probe(self):
        receipt = _receipt()
        frame = {"schema": 1, "engine": "linux/amd64", "services": {},
                 "images": {}, "config_error": None, "remote_receipt": None}
        entry = {"provisioned": True, "ssh": "example.test"}
        with patch.object(remote_local_images._remote, "get_remote", return_value=entry), \
             patch.object(remote_local_images._remote, "ssh_run", return_value=SimpleNamespace(
                 returncode=0, stdout=json.dumps(frame))):
            self.assertEqual(remote_local_images.observe("target", receipt), frame)

    def test_load_refuses_changed_archive_before_ssh(self):
        receipt = _receipt()
        artifact = receipt["artifacts"]["image-1"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "image-1.tar")
            path.write_bytes(b"changed")
            path.chmod(0o600)
            with patch.object(remote_local_images._remote, "get_remote",
                              return_value={"provisioned": True}) as lookup:
                with self.assertRaisesRegex(remote_local_images.RemoteLocalImageError,
                                            "archive_changed"):
                    remote_local_images.load_archive(
                        "target", path, artifact, timeout=1)
            lookup.assert_called_once()

    def test_read_receipt_requires_requested_self_digest(self):
        receipt = _receipt()
        document = local_images.remote_receipt_document(
            receipt, {"app:test": {"id": "sha256:" + "1" * 64,
                                    "artifact": "image-1"}},
            verified_at="2026-10-16T00:00:00Z")
        document.update({"machine_identity": "machine", "registered_host_digest": "host",
                         "daemon_identity": "daemon"})
        digest = local_images.canonical_digest(document)
        document["remote_receipt_digest"] = digest
        entry = {"provisioned": True, "ssh": "example.test"}
        with patch.object(remote_local_images._remote, "get_remote", return_value=entry), \
             patch.object(remote_local_images._remote, "resolve_sandbox_home",
                          return_value="/srv/sandbox"), \
             patch.object(remote_local_images._remote, "ssh_run", return_value=SimpleNamespace(
                 returncode=0, stdout=json.dumps(document))):
            self.assertEqual(remote_local_images.read_receipt("target", digest), document)

    def test_storage_floor_refusal_precedes_coordination_and_remote_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_receipt(directory)
            entry = {"provisioned": True, "storage_floor_gb": 30}
            refusal = remote_local_images.RemoteLocalImageError("remote_storage_floor")
            with patch.object(remote_local_images, "observe",
                              return_value=self._matching_observation()), \
                 patch.object(remote_local_images, "_entry", return_value=entry), \
                 patch.object(remote_local_images, "_storage_admission",
                              side_effect=refusal) as admission, \
                 patch.object(remote_local_images, "_TransferLease") as lease:
                with self.assertRaisesRegex(remote_local_images.RemoteLocalImageError,
                                            "remote_storage_floor"):
                    remote_local_images.push_receipt(
                        "target", path, timeout=60, artifact_timeout=30,
                        machine_identity="machine", registered_host_digest="host")
            admission.assert_called_once_with(
                entry, _receipt(), self._matching_observation(),
                timeout=ANY, before_write=ANY)
            lease.assert_not_called()

    def test_transfer_lock_is_held_by_registered_ssh_process_until_exit(self):
        process = MagicMock()
        process.stdin = MagicMock()
        process.stdout = MagicMock()
        process.stdout.readline.return_value = b'{"schema":1,"acquired":true}\n'
        process.poll.return_value = None
        selector = MagicMock()
        selector.select.return_value = [(process.stdout, 1)]
        entry = {"provisioned": True, "ssh": "example.test"}
        with patch.object(remote_local_images._remote, "resolve_sandbox_home",
                          return_value="/srv/sandbox"), \
             patch.object(remote_local_images._remote, "ssh_command_args",
                          return_value=["ssh", "fixed"]), \
             patch.object(remote_local_images.subprocess, "Popen",
                          return_value=process), \
             patch.object(remote_local_images.selectors, "DefaultSelector",
                          return_value=selector):
            with remote_local_images._TransferLease(entry, timeout=0.25) as lease:
                lease.require()
        selector.select.assert_called_once_with(0.25)
        process.stdin.close.assert_called_once()
        process.wait.assert_called_once_with(timeout=10)

    def test_orphan_load_fence_refuses_new_engine_owner_without_waiting(self):
        harness = """
import fcntl,os,sys,tempfile
from unittest.mock import patch
with tempfile.TemporaryDirectory() as root:
    fence=os.open(root+'/load-fence.lock',os.O_CREAT|os.O_RDWR,0o600)
    fcntl.flock(fence,fcntl.LOCK_EX|fcntl.LOCK_NB)
    # engine.lock is free, as after loss of the original lease's SSH process.
    with patch.object(sys,'argv',['worker',root]):
        try: exec(PROGRAM)
        except SystemExit as exc: assert exc.code==75
        else: raise AssertionError('admitted over orphaned load')
""".replace("PROGRAM", repr(remote_local_images._ENGINE_LOCK_PROGRAM))
        result = run_test_process([sys.executable, "-c", harness],
                                  capture_output=True, text=True, timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"schema": 1, "acquired": False})

    def test_stage_worker_holds_fence_during_upload_and_docker_load(self):
        harness = """
import fcntl,hashlib,json,os,sys,tempfile
from types import SimpleNamespace
from unittest.mock import patch
with tempfile.TemporaryDirectory() as sandbox_root:
    uploads=sandbox_root+'/uploads'
    data=b'fake-archive'
    def assert_fenced():
        with open(sandbox_root+'/load-fence.lock','rb') as other:
            try: fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: pass
            else: raise AssertionError('worker lost fence')
    def read(fd,size):
        assert fd==0
        assert_fenced()
        return chunks.pop(0)
    def load(argv,**kwargs):
        assert argv[:3]==['docker','image','load']
        assert_fenced()
        assert len(kwargs['pass_fds'])==1
        assert os.fstat(kwargs['pass_fds'][0])
        return SimpleNamespace(returncode=0)
    chunks=[data,b'']
    args=['worker',uploads,'image-1.tar','sha256:'+hashlib.sha256(data).hexdigest(),str(len(data))]
    with patch.object(sys,'argv',args), patch('os.read',read), patch('subprocess.run',load):
        exec(PROGRAM)
""".replace("PROGRAM", repr(remote_local_images._STAGE_PROGRAM))
        result = run_test_process([sys.executable, "-c", harness],
                                  capture_output=True, text=True, timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "loaded")

    def test_storage_admission_rejects_traversal_before_remote_probe(self):
        receipt = _receipt()
        receipt["project"] = "../../victim/deploy-src/app"
        receipt = local_images.seal_receipt(receipt)
        with patch("sandbox.resources.storage_floor.remote_storage_floor_admission") as probe:
            with self.assertRaises(local_images.LocalImageError):
                remote_local_images._storage_admission(
                    {"storage_floor_gb": 30}, receipt, self._matching_observation())
        probe.assert_not_called()

    def test_storage_admission_uses_contained_target_and_budget(self):
        entry = {"storage_floor_gb": 30}
        with patch.object(remote_local_images._remote, "resolve_sandbox_home",
                          return_value="/srv/sandbox/"), \
             patch("sandbox.resources.storage_floor.remote_storage_floor_admission",
                   return_value={"ok": True}) as probe:
            remote_local_images._storage_admission(
                entry, _receipt(), self._matching_observation(), timeout=0.25)
        self.assertEqual(probe.call_args.args, (entry, "/srv/sandbox/deploy-src/app"))
        self.assertEqual(probe.call_args.kwargs["timeout"], 0.25)

    @contextmanager
    def _push_steps(self, *, elapsed=None, floor=False, initial_delay=0):
        elapsed = elapsed or {}
        clock = [0.0]
        calls = []
        entry = {"provisioned": True, "host": "original"}
        if floor:
            entry["storage_floor_gb"] = 30
        good = self._matching_observation()
        absent = {**good, "images": {}}
        counts = {"observe": 0, "daemon": 0}
        def step(name, kwargs):
            budget = kwargs["timeout"]
            self.assertGreater(budget, 0)
            self.assertLessEqual(budget, 60 - clock[0])
            calls.append((name, budget))
            clock[0] += elapsed.get(name, 0)
        def observe(_remote, _receipt, **kwargs):
            name = ("initial_observe", "reobserve", "post_load_observe")[counts["observe"]]
            counts["observe"] += 1
            self.assertIs(kwargs["entry"], entry)
            step(name, kwargs)
            return good if name == "post_load_observe" else absent
        def admission(_entry, _receipt, _observation, **kwargs):
            self.assertIs(_entry, entry)
            if floor:
                kwargs["before_write"]()
            step("admission", kwargs)
        lease = MagicMock()
        def acquire(*_args, **kwargs):
            self.assertIs(kwargs["entry"], entry)
            step("lease", kwargs)
            return lease
        def daemon(_entry, _command, **kwargs):
            self.assertIs(_entry, entry)
            name = ("daemon_before", "daemon_after")[counts["daemon"]]
            counts["daemon"] += 1
            step(name, kwargs)
            return "daemon"
        def upload(*_args, **kwargs):
            self.assertIs(kwargs["entry"], entry)
            step("load", kwargs)
        def publish(*_args, **kwargs):
            self.assertIs(kwargs["entry"], entry)
            step("publish", kwargs)
            return "sha256:" + "f" * 64
        with ExitStack() as stack:
            for name, function in (("observe", observe), ("_storage_admission", admission),
                                   ("target_image_lease", acquire), ("_checked", daemon),
                                   ("load_archive", upload), ("publish", publish)):
                stack.enter_context(patch.object(remote_local_images, name, side_effect=function))
            lookup = stack.enter_context(patch.object(remote_local_images, "_entry", return_value=entry))
            stack.enter_context(patch.object(remote_local_images.time, "monotonic", side_effect=lambda: clock[0]))
            def load_receipt(_path):
                clock[0] += initial_delay
                return _receipt(), Path("/synthetic/receipt.json")
            stack.enter_context(patch.object(local_images, "load_receipt", side_effect=load_receipt))
            yield calls, lookup, entry

    def _push(self, **kwargs):
        return remote_local_images.push_receipt(
            "target", "/synthetic/receipt.json", timeout=60, artifact_timeout=30,
            machine_identity="machine", registered_host_digest="host", **kwargs)

    def test_transfer_resolves_registration_once_and_pins_every_step(self):
        with self._push_steps() as (calls, lookup, _entry):
            result = self._push()
        lookup.assert_called_once_with("target")
        self.assertEqual(result["loaded"], ["image-1"])
        self.assertEqual([name for name, _budget in calls], [
            "initial_observe", "admission", "lease", "reobserve", "daemon_before",
            "load", "post_load_observe", "daemon_after", "publish"])

    def test_transfer_with_explicit_entry_never_resolves_registration(self):
        with self._push_steps() as (_calls, lookup, entry):
            self._push(entry=entry)
        lookup.assert_not_called()

    def test_every_step_recomputes_and_caps_remaining_deadline(self):
        elapsed = dict(zip(("initial_observe", "admission", "lease", "reobserve",
                            "daemon_before", "load", "post_load_observe", "daemon_after",
                            "publish"), (2, 3, 4, 5, 6, 10, 2, 3, 1)))
        with self._push_steps(elapsed=elapsed) as (calls, _lookup, _entry):
            self._push()
        self.assertEqual(calls, [
            ("initial_observe", 60), ("admission", 58), ("lease", 30),
            ("reobserve", 51), ("daemon_before", 30), ("load", 30),
            ("post_load_observe", 30), ("daemon_after", 28), ("publish", 25)])

    def test_deadline_exhaustion_at_every_step_never_runs_next_step(self):
        for name in ("initial_observe", "admission", "lease", "reobserve",
                     "daemon_before", "load", "post_load_observe", "daemon_after", "publish"):
            with self.subTest(step=name), self._push_steps(elapsed={name: 61}) as (calls, _, _entry):
                expected = "transfer_timeout" if name in ("initial_observe", "admission") else "transfer_unknown"
                with self.assertRaisesRegex(remote_local_images.RemoteLocalImageError, expected):
                    self._push()
                self.assertEqual(calls[-1][0], name)

    def test_deadline_before_initial_probe_is_clean_timeout(self):
        with self._push_steps(initial_delay=61) as (calls, _lookup, _entry):
            with self.assertRaisesRegex(remote_local_images.RemoteLocalImageError, "transfer_timeout"):
                self._push()
            self.assertEqual(calls, [])

    def test_storage_reservation_deadline_is_unknown_after_possible_write(self):
        with self._push_steps(elapsed={"admission": 61}, floor=True) as (calls, _, _entry):
            with self.assertRaisesRegex(remote_local_images.RemoteLocalImageError, "transfer_unknown"):
                self._push()
        self.assertEqual(calls[-1][0], "admission")

    def test_probe_timeout_before_any_write_is_clean_refusal(self):
        with self._push_steps(), patch.object(remote_local_images, "observe",
                side_effect=subprocess.TimeoutExpired("fake-probe", 1)):
            with self.assertRaisesRegex(remote_local_images.RemoteLocalImageError, "transfer_timeout"):
                self._push()

    def test_explicit_entry_is_reused_by_actual_observe_load_and_publish(self):
        entry = {"provisioned": True, "host": "original"}
        good = self._matching_observation()
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            lookup = stack.enter_context(patch.object(remote_local_images, "_entry",
                side_effect=AssertionError("registration was resolved again")))
            stack.enter_context(patch.object(remote_local_images._remote,
                "resolve_sandbox_home", return_value="/srv/sandbox"))
            ssh = stack.enter_context(patch.object(remote_local_images._remote, "ssh_run",
                return_value=SimpleNamespace(returncode=0, stdout=json.dumps(good))))
            remote_local_images.observe("target", _receipt(), entry=entry)
            self.assertIs(ssh.call_args.args[0], entry)
            archive = Path(directory, "image-1.tar")
            archive.write_bytes(b"archive")
            archive.chmod(0o600)
            digest, size = local_images.file_sha256(archive)
            artifact = {**_receipt()["artifacts"]["image-1"],
                        "archive_sha256": digest, "archive_bytes": size}
            args = stack.enter_context(patch.object(remote_local_images._remote,
                "ssh_command_args", return_value=["fake-ssh"]))
            stack.enter_context(patch.object(local_images, "stream_archive", return_value={
                "timed_out": False, "cancelled": False, "broken_pipe": False,
                "returncode": 0, "sha256": digest, "output_tail": json.dumps({
                    "schema": 1, "state": "loaded", "bytes": size})}))
            remote_local_images.load_archive("target", archive, artifact, timeout=1, entry=entry)
            self.assertIs(args.call_args.args[0], entry)
            document = {"synthetic": "document"}
            want = local_images.canonical_digest(document)
            ssh.return_value.stdout = json.dumps({"schema": 1, "remote_receipt_digest": want})
            self.assertEqual(remote_local_images.publish("target", document, entry=entry), want)
            self.assertIs(ssh.call_args.args[0], entry)
            lookup.assert_not_called()


class SharedBundleTransferTests(unittest.TestCase):
    def test_partial_receiver_reserves_full_bundle_staging_and_import(self):
        from tests.test_hosting_local_images import SharedBundleReceiptTests
        receipt = SharedBundleReceiptTests.receipt()
        receipt['bundles']['bundle-1']['archive_bytes'] = 100_000_000
        receipt = local_images.seal_receipt(receipt)
        with patch.object(remote_local_images._remote, 'resolve_sandbox_home', return_value='/srv/sandbox'), \
             patch('sandbox.resources.storage_floor.remote_storage_floor_admission',
                   return_value={'ok':True}) as admission:
            remote_local_images._storage_admission({'storage_floor_gb':1}, receipt, {'images':{}})
        self.assertEqual(admission.call_args.kwargs['minimum_estimate_bytes'],
                         300_000_000)

    def test_v2_remote_reader_binds_the_version_of_nested_local_receipt(self):
        from tests.test_hosting_local_images import SharedBundleReceiptTests
        receipt = SharedBundleReceiptTests.receipt()
        document = local_images.remote_receipt_document(receipt, {'app:test':{'id':'sha256:'+'1'*64, 'artifact':'image-1'}}, verified_at='synthetic')
        document.update(machine_identity='synthetic', registered_host_digest='synthetic',
                        daemon_identity='synthetic')
        for schema, valid in ((local_images.REMOTE_RECEIPT_SCHEMA_V2, True),
                              (local_images.REMOTE_RECEIPT_SCHEMA, False)):
            document['schema'] = schema
            digest = local_images.canonical_digest({k:v for k,v in document.items()
                                                   if k != 'remote_receipt_digest'})
            document['remote_receipt_digest'] = digest
            with patch.object(remote_local_images, '_checked', return_value=json.dumps(document)), \
                 patch.object(remote_local_images._remote, 'resolve_sandbox_home', return_value='/srv/sandbox'):
                if valid:
                    self.assertEqual(remote_local_images.read_receipt('local',digest,entry={}), document)
                else:
                    with self.assertRaises(remote_local_images.RemoteLocalImageError):
                        remote_local_images.read_receipt('local',digest,entry={})

    def test_v2_partial_uploads_once_and_all_present_uploads_nothing(self):
        from tests.test_hosting_local_images import SharedBundleReceiptTests
        receipt = SharedBundleReceiptTests.receipt()
        match = RemoteLocalImageTests._matching_observation()
        for present, expected_loads in ((False, 1), (True, 0)):
            initial = match if present else {'engine':'linux/amd64', 'images':{}}
            lease = MagicMock()
            lease.__enter__.return_value = lease
            with ExitStack() as stack:
                stack.enter_context(patch.object(local_images, 'load_receipt', return_value=(receipt, Path('/synthetic/receipt.json'))))
                stack.enter_context(patch.object(local_images, 'bundle_identities'))
                stack.enter_context(patch.object(remote_local_images, 'observe', side_effect=[initial, initial, match] if not present else [match, match]))
                stack.enter_context(patch.object(remote_local_images, '_storage_admission', return_value=None))
                stack.enter_context(patch.object(remote_local_images, "_reconcile_reservations", return_value=True))
                stack.enter_context(patch.object(remote_local_images, 'target_image_lease', return_value=lease))
                stack.enter_context(patch.object(remote_local_images, '_checked', return_value='same-daemon'))
                load = stack.enter_context(patch.object(remote_local_images, 'load_archive'))
                stack.enter_context(patch.object(remote_local_images, 'publish', return_value='synthetic-remote-digest'))
                value = remote_local_images.push_receipt('synthetic', '/synthetic/receipt.json', timeout=60,
                    artifact_timeout=60, machine_identity='synthetic', registered_host_digest='synthetic', entry={})
            self.assertEqual(load.call_count, expected_loads)
            self.assertEqual(value['transferred_bytes'], 10 * expected_loads)
            self.assertEqual(value['transferred_bundles'], ['bundle-1'] if expected_loads else [])
            self.assertEqual(value['loaded'], ['image-1'] if expected_loads else [])
            self.assertEqual(value['skipped'], [] if expected_loads else ['image-1'])

    def test_v2_remote_receipt_requires_complete_inventory_and_nested_continuity(self):
        from tests.test_hosting_local_images import SharedBundleReceiptTests
        receipt = SharedBundleReceiptTests.receipt()
        verified = {'app:test':{'id':'sha256:'+'1'*64, 'artifact':'image-1'}}
        document = local_images.remote_receipt_document(receipt, verified, verified_at='synthetic')
        local_images.validate_remote_v2(document)
        for field, bad in (('images', {}), ('commit', 'b'*40),
                           ('receipt_digest', 'sha256:'+'b'*64)):
            with self.subTest(field=field), self.assertRaises(local_images.LocalImageError):
                local_images.validate_remote_v2({**document, field:bad})


class BundleReviewAdmissionTests(unittest.TestCase):
    def test_v2_default_floor_is_mandatory_and_reserves_three_copies(self):
        from tests.test_hosting_local_images import SharedBundleReceiptTests
        value = SharedBundleReceiptTests.receipt()
        value['bundles']['bundle-1']['archive_bytes'] = 100_000_000
        value = local_images.seal_receipt(value)
        for floor in (None,0,5):
            entry = {} if floor is None else {'storage_floor_gb':floor}
            with self.subTest(floor=floor), \
                 patch.object(remote_local_images._remote,'resolve_sandbox_home',return_value='/srv/sandbox'), \
                 patch('sandbox.resources.storage_floor.remote_storage_floor_admission',return_value={'ok':True}) as admission:
                remote_local_images._storage_admission(entry,value,{'images':{}})
            self.assertEqual(admission.call_args.args[0]['storage_floor_gb'],floor or 1)
            self.assertEqual(admission.call_args.kwargs['minimum_estimate_bytes'],300_000_000)
            self.assertEqual(entry,{} if floor is None else {'storage_floor_gb':floor})
        with patch.object(remote_local_images._remote,'resolve_sandbox_home',return_value='/srv/sandbox'), \
             patch('sandbox.resources.storage_floor.remote_storage_floor_admission',return_value=None):
            with self.assertRaises(remote_local_images.RemoteLocalImageError):
                remote_local_images._storage_admission({},value,{'images':{}})

    def test_bundle_load_budget_scales_and_remote_timeout_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory,'bundle-1.tar')
            path.write_bytes(b'x'*(2*1048576+1)); path.chmod(0o600)
            digest,size = local_images.file_sha256(path)
            bundle = {'archive':path.name,'archive_bytes':size,'archive_sha256':digest,'members':['image-1']}
            for code in (0,34,137):
                result = {'returncode':code,'timed_out':False,'cancelled':False,'broken_pipe':False,
                          'sha256':digest,'output_tail':json.dumps({'schema':1,'state':'loaded','bytes':size})}
                with patch.object(remote_local_images._remote,'resolve_sandbox_home',return_value='/srv/sandbox'), \
                     patch.object(remote_local_images._remote,'ssh_command_args',return_value=['synthetic']) as command, \
                     patch.object(local_images,'stream_archive',return_value=result):
                    if code:
                        with self.assertRaises(remote_local_images.RemoteLocalImageError) as raised:
                            remote_local_images.load_archive('local',path,bundle,timeout=60,entry={})
                        self.assertEqual(raised.exception.code,'local_images_transfer_unknown')
                    else:
                        remote_local_images.load_archive('local',path,bundle,timeout=60,entry={})
                self.assertEqual(shlex.split(command.call_args.args[1])[-1],'1803')

    def test_load_timeout_and_worker_loss_keep_persistent_quarantine(self):
        harness = """
import hashlib,json,os,stat,subprocess,sys,tempfile
from types import SimpleNamespace
from unittest.mock import patch
for outcome in ('success','timeout','worker-loss'):
 with tempfile.TemporaryDirectory() as root:
  data=b'archive';chunks=[data,b''];scope={}
  def load(argv,**kwargs):
   assert kwargs['timeout']==37
   assert os.path.exists(root+'/load-unverified')
   assert stat.S_IMODE(os.stat(root+'/load-fence.lock').st_mode)==0o400
   if outcome=='timeout': raise subprocess.TimeoutExpired(argv,37)
   if outcome=='worker-loss': raise SystemExit(99)
   return SimpleNamespace(returncode=0)
  args=['worker',root+'/uploads','bundle-1.tar','sha256:'+hashlib.sha256(data).hexdigest(),str(len(data)),'37']
  with patch.object(sys,'argv',args),patch('os.read',side_effect=lambda *_:chunks.pop(0)),patch('subprocess.run',side_effect=load):
   try: exec(__STAGE_PROGRAM__,scope)
   except SystemExit as exc: assert exc.code==({'timeout':34,'worker-loss':99}[outcome])
   else: assert outcome=='success'
  os.close(scope['fence'])
  assert not os.listdir(root+'/uploads')
  if outcome=='success':
   assert not os.path.exists(root+'/load-unverified')
   assert stat.S_IMODE(os.stat(root+'/load-fence.lock').st_mode)==0o600
  else:
   assert os.path.exists(root+'/load-unverified')
   with patch.object(sys,'argv',['lock',root]):
    try: exec(__LOCK_PROGRAM__,{})
    except SystemExit as exc: assert exc.code==75
    else: raise AssertionError('admitted after unverified load')
""".replace('__STAGE_PROGRAM__',repr(remote_local_images._STAGE_PROGRAM)).replace('__LOCK_PROGRAM__',repr(remote_local_images._ENGINE_LOCK_PROGRAM))
        result = run_test_process([sys.executable,'-c',harness],capture_output=True,text=True,timeout=3)
        self.assertEqual(result.returncode,0,result.stderr)
        frames = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(frames[0]['state'],'loaded')
        self.assertEqual(frames[1:],[{'schema':1,'acquired':False}]*2)



class BundleReservationRaceTests(unittest.TestCase):
    def test_first_present_then_locked_mismatch_has_full_reservation_or_refuses(self):
        from tests.test_hosting_local_images import SharedBundleReceiptTests
        receipt = SharedBundleReceiptTests.receipt()
        receipt['bundles']['bundle-1']['archive_bytes'] = 100_000_000
        receipt = local_images.seal_receipt(receipt)
        matching = RemoteLocalImageTests._matching_observation()
        missing = {'engine':'linux/amd64','images':{}}
        for admit in (True,False):
            lease = MagicMock(); lease.__enter__.return_value = lease
            with self.subTest(admit=admit), ExitStack() as stack:
                stack.enter_context(patch.object(local_images,'load_receipt',return_value=(receipt,Path('/synthetic/receipt.json'))))
                stack.enter_context(patch.object(local_images,'bundle_identities'))
                stack.enter_context(patch.object(remote_local_images,'observe',side_effect=[matching,missing,matching]))
                stack.enter_context(patch.object(remote_local_images._remote,'resolve_sandbox_home',return_value='/srv/sandbox'))
                policy = stack.enter_context(patch('sandbox.resources.storage_floor.remote_storage_floor_admission',return_value={'ok':admit}))
                stack.enter_context(patch.object(remote_local_images, "_reconcile_reservations", return_value=True))
                acquire = stack.enter_context(patch.object(remote_local_images,'target_image_lease',return_value=lease))
                stack.enter_context(patch.object(remote_local_images,'_checked',return_value='same-daemon'))
                upload = stack.enter_context(patch.object(remote_local_images,'load_archive'))
                publish = stack.enter_context(patch.object(remote_local_images,'publish',return_value='synthetic'))
                release = stack.enter_context(patch.object(remote_local_images,'_release_reservation'))
                if admit:
                    result = remote_local_images.push_receipt('synthetic','unused',timeout=60,
                        artifact_timeout=30,machine_identity='synthetic',registered_host_digest='synthetic',entry={})
                    self.assertEqual(result['loaded'],['image-1'])
                    upload.assert_called_once()
                    self.assertEqual(upload.call_args.kwargs['reservation_id'],policy.call_args.kwargs['reservation_id'])
                    release.assert_not_called()  # Worker acknowledged its own durable release.
                else:
                    with self.assertRaises(remote_local_images.RemoteLocalImageError):
                        remote_local_images.push_receipt('synthetic','unused',timeout=60,
                            artifact_timeout=30,machine_identity='synthetic',registered_host_digest='synthetic',entry={})
                    acquire.assert_called_once(); upload.assert_not_called(); publish.assert_not_called()
                self.assertEqual(policy.call_args.kwargs['minimum_estimate_bytes'],300_000_000)
                self.assertRegex(policy.call_args.kwargs['reservation_id'],r'^[0-9a-f]{32}$')

    def test_no_worker_releases_but_uncertain_worker_keeps_reservation(self):
        from tests.test_hosting_local_images import SharedBundleReceiptTests
        receipt = SharedBundleReceiptTests.receipt()
        matching = RemoteLocalImageTests._matching_observation()
        def interrupted(*args, **kwargs):
            kwargs['on_started']()
            raise remote_local_images.RemoteLocalImageError('local_images_transfer_unknown')
        for starts in (False,True):
            locked = {'engine':'linux/amd64','images':{}} if starts else matching
            lease = MagicMock(); lease.__enter__.return_value = lease
            with self.subTest(starts=starts), ExitStack() as stack:
                stack.enter_context(patch.object(local_images,'load_receipt',return_value=(receipt,Path('/synthetic/receipt.json'))))
                stack.enter_context(patch.object(local_images,'bundle_identities'))
                stack.enter_context(patch.object(remote_local_images,'observe',side_effect=[matching,locked]))
                stack.enter_context(patch.object(remote_local_images,'_storage_admission',return_value='a'*32))
                stack.enter_context(patch.object(remote_local_images, "_reconcile_reservations", side_effect=[True,False] if starts else [True]))
                stack.enter_context(patch.object(remote_local_images,'target_image_lease',return_value=lease))
                stack.enter_context(patch.object(remote_local_images,'_checked',return_value='same-daemon'))
                stack.enter_context(patch.object(remote_local_images,'load_archive',side_effect=interrupted))
                stack.enter_context(patch.object(remote_local_images,'publish',return_value='synthetic'))
                release = stack.enter_context(patch.object(remote_local_images,'_release_reservation'))
                if starts:
                    with self.assertRaises(remote_local_images.RemoteLocalImageError):
                        remote_local_images.push_receipt('synthetic','unused',timeout=60,artifact_timeout=30,
                            machine_identity='synthetic',registered_host_digest='synthetic',entry={})
                    release.assert_not_called()
                else:
                    remote_local_images.push_receipt('synthetic','unused',timeout=60,artifact_timeout=30,
                        machine_identity='synthetic',registered_host_digest='synthetic',entry={})
                    release.assert_called_once_with({},receipt,'a'*32)



class FinalReservationTests(unittest.TestCase):
    @contextmanager
    def controller(self, directory, *, matching=False, cleanup_error=False):
        import hashlib
        from tests.test_hosting_local_images import SharedBundleReceiptTests
        receipt=SharedBundleReceiptTests.receipt()
        data=b'archive'
        path=Path(directory,'bundle-1.tar');path.write_bytes(data);path.chmod(0o600)
        receipt['bundles']['bundle-1'].update(archive_sha256='sha256:'+hashlib.sha256(data).hexdigest(),archive_bytes=len(data))
        receipt=local_images.seal_receipt(receipt)
        present=RemoteLocalImageTests._matching_observation()
        observation=present if matching else {'engine':'linux/amd64','images':{}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(local_images,'load_receipt',return_value=(receipt,Path(directory,'receipt.json'))))
            stack.enter_context(patch.object(local_images,'bundle_identities'))
            stack.enter_context(patch.object(remote_local_images,'observe',return_value=observation))
            lease=MagicMock();lease.__enter__.return_value=lease;lease.lost.return_value=False
            stack.enter_context(patch.object(remote_local_images,'target_image_lease',return_value=lease))
            reconcile=stack.enter_context(patch.object(remote_local_images,'_reconcile_reservations',return_value=True))
            admission=stack.enter_context(patch.object(remote_local_images,'_storage_admission',return_value='a'*32))
            release=stack.enter_context(patch.object(remote_local_images,'_release_reservation',side_effect=OSError('cleanup failed') if cleanup_error else None))
            stack.enter_context(patch.object(remote_local_images,'_checked',return_value='same-daemon'))
            stack.enter_context(patch.object(remote_local_images._remote,'resolve_sandbox_home',return_value=directory))
            publish=stack.enter_context(patch.object(remote_local_images,'publish',return_value='published'))
            real_popen=subprocess.Popen
            def spawn(*args,**kwargs):
                kwargs['env']=synthetic_environment({'PATH':'/nonexistent'})
                return real_popen(*args,**kwargs)
            stack.enter_context(patch.object(local_images.subprocess,'Popen',side_effect=spawn))
            yield receipt,path,release,reconcile,admission,publish

    @staticmethod
    def push(**kwargs):
        return remote_local_images.push_receipt('local','unused',timeout=30,artifact_timeout=kwargs.pop('artifact_timeout',10),
            machine_identity='synthetic',registered_host_digest='synthetic',entry={},**kwargs)

    def test_local_and_unacknowledged_remote_failures_release(self):
        for failure in ('archive-changed','deadline','entry',255,31,62,75):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as directory, \
                 self.controller(directory) as (receipt,path,release,reconcile,admission,publish),ExitStack() as stack:
                if failure=='archive-changed': path.write_bytes(b'changed')
                if failure=='entry':
                    stack.enter_context(patch.object(remote_local_images,'_local_image_root',side_effect=remote_local_images.RemoteLocalImageError('local_images_remote_unavailable')))
                argv=[sys.executable,'-c','import sys;sys.exit('+str(failure if isinstance(failure,int) else 255)+')']
                ssh=stack.enter_context(patch.object(remote_local_images._remote,'ssh_command_args',return_value=argv))
                with self.assertRaises(remote_local_images.RemoteLocalImageError) as raised:
                    self.push(artifact_timeout=0 if failure=='deadline' else 10)
                release.assert_called_once_with({},receipt,'a'*32)
                publish.assert_not_called()
                if failure in ('archive-changed','deadline','entry'):ssh.assert_not_called()
                if failure=='archive-changed':self.assertEqual(raised.exception.code,'local_images_archive_changed')

    def test_cleanup_failure_preserves_error_and_successful_publication(self):
        with tempfile.TemporaryDirectory() as directory,self.controller(directory,cleanup_error=True) as (_,path,release,_,_,_):
            path.write_bytes(b'changed')
            with self.assertRaises(remote_local_images.RemoteLocalImageError) as raised:
                self.push(progress=lambda _: (_ for _ in ()).throw(ValueError('progress failed')))
            self.assertEqual(raised.exception.code,'local_images_archive_changed')
            release.assert_called_once()
        with tempfile.TemporaryDirectory() as directory,self.controller(directory,matching=True,cleanup_error=True) as (_,_,release,_,_,publish):
            result=self.push()
            self.assertEqual(result['remote_receipt_digest'],'published')
            self.assertTrue(result['reservation_cleanup_pending'])
            publish.assert_called_once();release.assert_called_once()

    def test_orphan_reconcile_precedes_admission_and_floor_message_has_fallback(self):
        events=[]
        with tempfile.TemporaryDirectory() as directory,self.controller(directory) as (_,_,_,reconcile,admission,_):
            reconcile.side_effect=lambda *a,**k: events.append('reconcile') or True
            admission.side_effect=lambda *a,**k: events.append('admission') or (_ for _ in ()).throw(remote_local_images.RemoteLocalImageError('remote_storage_floor'))
            with self.assertRaises(remote_local_images.RemoteLocalImageError):self.push()
        self.assertEqual(events,['reconcile','admission'])
        from tests.test_hosting_local_images import SharedBundleReceiptTests
        receipt=SharedBundleReceiptTests.receipt()
        with patch.object(remote_local_images._remote,'resolve_sandbox_home',return_value='/srv/sandbox'), \
             patch('sandbox.resources.storage_floor.remote_storage_floor_admission',return_value={'ok':False}):
            with self.assertRaises(remote_local_images.RemoteLocalImageError) as raised:
                remote_local_images._storage_admission({},receipt,RemoteLocalImageTests._matching_observation())
        self.assertIn('storage floor',str(raised.exception));self.assertIn('3x',str(raised.exception));self.assertIn('v1',str(raised.exception))

    def test_stream_sends_nothing_until_exact_worker_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory,'bundle.tar');path.write_bytes(b'archive')
            frame={'schema':1,'state':'ready','reservation_id':'a'*32}
            real_popen=subprocess.Popen
            def spawn(*args,**kwargs):
                kwargs['env']=synthetic_environment({'PATH':'/nonexistent'})
                return real_popen(*args,**kwargs)
            for valid in (False,True):
                script=("import sys,json\n"+('print('+repr(__import__('json').dumps(frame))+',flush=True)\n' if valid else 'sys.exit(255)\n')+
                        "data=sys.stdin.buffer.read()\nprint(json.dumps({'bytes':len(data)}))\n")
                started=[]
                with patch.object(local_images.subprocess,'Popen',side_effect=spawn):
                    result=local_images.stream_archive([sys.executable,'-c',script],path,timeout=2,ready_frame=frame,on_started=lambda:started.append(True))
                self.assertEqual(started,[True] if valid else [])
                self.assertEqual(result['sent_bytes'],7 if valid else 0)

    def test_pinned_old_controller_still_refuses_quarantine(self):
        from tests.fixtures.local_image_legacy_programs import ENGINE_LOCK_V1
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'load-fence.lock').touch(mode=0o400)
            Path(directory,'load-unverified').write_text('unverified')
            result=run_test_process([sys.executable,'-c',ENGINE_LOCK_V1,directory],input='',capture_output=True,text=True,timeout=2)
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn('"acquired":true',result.stdout)


if __name__ == "__main__":
    unittest.main()
