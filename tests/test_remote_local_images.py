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
    def test_legacy_writable_ledger_reports_reconciliation_error_and_succeeds_after_manual_chmod(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "runtime", "host-local-images")
            root.mkdir(parents=True, mode=0o700)
            (root / "load-fence.lock").touch(mode=0o600)
            store = root.parent / "storage"
            store.mkdir(mode=0o700)
            ledger = store / "reservations.json"
            ledger.write_text("[]")
            ledger.chmod(0o664)

            def local_ssh(_entry, command, **_kwargs):
                argv = shlex.split(command)
                return run_test_process([sys.executable, *argv[1:]],
                                        capture_output=True, text=True)

            with patch.object(remote_local_images._remote, "resolve_sandbox_home",
                              return_value=directory), \
                 patch.object(remote_local_images._remote, "ssh_run", side_effect=local_ssh):
                with self.assertRaises(remote_local_images.RemoteLocalImageError) as raised:
                    remote_local_images._reconcile_reservations({}, {"project": "lenzora"})
                self.assertEqual(raised.exception.code, "local_images_remote_failed")
                self.assertIn("remote command exited 1", str(raised.exception))
                self.assertIn("ValueError: unsafe reservation ledger", str(raised.exception))
                self.assertEqual(ledger.stat().st_mode & 0o777, 0o664)
                ledger.chmod(0o600)
                self.assertTrue(remote_local_images._reconcile_reservations(
                    {}, {"project": "lenzora"}))
                self.assertEqual(ledger.read_text(), "[]")

    def test_remote_failure_diagnostic_is_bounded_and_redacts_credentials_and_ssh_target(self):
        target = "synthetic-user@198.51.100.20"
        secret = "synthetic-private-bearer-value"
        diagnostic = ("x" * 2000 + "\nAuthorization: Bearer " + secret
                      + "\n" + target + "\nValueError: unsafe reservation ledger")
        for value in (diagnostic, diagnostic.encode()):
            with self.subTest(kind=type(value).__name__), \
                 patch.object(remote_local_images._remote, "ssh_run", return_value=SimpleNamespace(
                     returncode=255, stdout="", stderr=value)):
                with self.assertRaises(remote_local_images.RemoteLocalImageError) as raised:
                    remote_local_images._checked({"ssh": target}, "do-not-echo-command", timeout=5)
                message = str(raised.exception)
                self.assertIn("remote command exited 255", message)
                self.assertIn("unsafe reservation ledger", message)
                self.assertLess(len(message), 450)
                for private in (secret, target, "do-not-echo-command"):
                    self.assertNotIn(private, message)

    def test_empty_remote_failure_still_reports_exit_status(self):
        with patch.object(remote_local_images._remote, "ssh_run", return_value=SimpleNamespace(
                returncode=71, stdout="", stderr="")):
            with self.assertRaises(remote_local_images.RemoteLocalImageError) as raised:
                remote_local_images._checked({}, "do-not-echo-command", timeout=5)
        self.assertEqual(str(raised.exception),
                         "local_images_remote_failed: remote command exited 71")

    def test_remote_stdout_is_never_included_in_error(self):
        for stderr in ("", " ", b""):
            with self.subTest(stderr=stderr), \
                 patch.object(remote_local_images._remote, "ssh_run", return_value=SimpleNamespace(
                     returncode=71, stdout='{"machine_identity":"private-stdout-value"}', stderr=stderr)):
                with self.assertRaises(remote_local_images.RemoteLocalImageError) as raised:
                    remote_local_images._checked({}, "do-not-echo-command", timeout=5)
                self.assertEqual(str(raised.exception),
                                 "local_images_remote_failed: remote command exited 71")
                self.assertNotIn("private-stdout-value", str(raised.exception))

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

    def test_publish_accepts_real_binary_subprocess_acknowledgement(self):
        document = {"synthetic": "publication"}
        want = local_images.canonical_digest(document)
        with tempfile.TemporaryDirectory() as directory:
            entry = {"provisioned": True}

            def local_command(_entry, command, **_kwargs):
                argv = shlex.split(command)
                self.assertEqual(argv[0], "python3")
                return [sys.executable, *argv[1:]]

            with patch.object(remote_local_images._remote, "resolve_sandbox_home",
                              return_value=directory), \
                 patch.object(remote_local_images._remote, "_ensure_ssh_control_dir"), \
                 patch.object(remote_local_images._remote, "ssh_command_args",
                              side_effect=local_command), \
                 patch.object(remote_local_images._remote, "subprocess",
                              SimpleNamespace(run=run_test_process)):
                path = Path(directory, "runtime/host-local-images/receipts",
                            want.split(":", 1)[1] + ".json")
                expected = json.dumps({**document, "remote_receipt_digest": want},
                                      sort_keys=True, separators=(",", ":")).encode()
                for _ in range(2):
                    self.assertEqual(remote_local_images.publish(
                        "target", document, entry=entry), want)
                    # Exact canonical bytes after each publish: the second is idempotent.
                    self.assertEqual(path.read_bytes(), expected)
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_publish_invalid_acknowledgement_can_follow_receipt_write(self):
        document = {"synthetic": "publication"}
        want = local_images.canonical_digest(document)
        with tempfile.TemporaryDirectory() as directory:
            program = remote_local_images._PUBLISH_PROGRAM + "\nsys.stdout.buffer.write(b'\\xff')"
            with patch.object(remote_local_images._remote, "resolve_sandbox_home",
                              return_value=directory), \
                 patch.object(remote_local_images, "_PUBLISH_PROGRAM", program), \
                 patch.object(remote_local_images._remote, "_ensure_ssh_control_dir"), \
                 patch.object(remote_local_images._remote, "ssh_command_args",
                              side_effect=lambda _entry, command, **_kwargs:
                                  [sys.executable, *shlex.split(command)[1:]]), \
                 patch.object(remote_local_images._remote, "subprocess",
                              SimpleNamespace(run=run_test_process)):
                with self.assertRaisesRegex(remote_local_images.RemoteLocalImageError,
                                            "local_images_receipt_publish_unknown"):
                    remote_local_images.publish("target", document,
                                                entry={"provisioned": True})
            path = Path(directory, "runtime/host-local-images/receipts",
                        want.split(":", 1)[1] + ".json")
            self.assertEqual(json.loads(path.read_bytes()), {
                **document, "remote_receipt_digest": want})

    def test_publish_reply_validation_accepts_text_and_refuses_invalid_replies(self):
        document = {"synthetic": "publication"}
        want = local_images.canonical_digest(document)
        frame = json.dumps({"schema": 1, "remote_receipt_digest": want})
        replies = [None, b"", b"not-json", b"\xff", b"[]",
                   b"[" * 2000 + b"0" + b"]" * 2000,
                   json.dumps({"schema": 1, "remote_receipt_digest": want, "extra": True}).encode(),
                   json.dumps({"schema": 1}).encode(),
                   json.dumps({"schema": 2, "remote_receipt_digest": want}).encode(),
                   json.dumps({"schema": True, "remote_receipt_digest": want}).encode(),
                   json.dumps({"schema": 1.0, "remote_receipt_digest": want}).encode(),
                   ('{"schema":1,"schema":1,"remote_receipt_digest":"' + want + '"}').encode(),
                   ('{"schema":2,"schema":1,"remote_receipt_digest":"' + want + '"}').encode(),
                   ('{"schema":1,"remote_receipt_digest":"' + want +
                    '","remote_receipt_digest":"' + want + '"}').encode(),
                   ('{"schema":1,"remote_receipt_digest":"wrong",'
                    '"remote_receipt_digest":"' + want + '"}').encode(),
                   json.dumps({"schema": 1, "remote_receipt_digest": "wrong"}).encode(),
                   b" " * (local_images.MAX_CONFIG_BYTES + 1), "\ud800", bytearray(b"{}")]
        replies += [reply.decode("utf-8") for reply in replies
                    if isinstance(reply, bytes) and reply != b"\xff"]
        padded = frame + " " * (local_images.MAX_CONFIG_BYTES - len(frame.encode()))
        with patch.object(remote_local_images._remote, "resolve_sandbox_home",
                          return_value="/synthetic"), \
             patch.object(remote_local_images._remote, "ssh_run") as ssh:
            for output in [frame, frame.encode(), padded, padded.encode()]:
                ssh.return_value = SimpleNamespace(returncode=0, stdout=output)
                self.assertEqual(remote_local_images.publish(
                    "target", document, entry={"provisioned": True}), want)
                self.assertIsInstance(ssh.call_args.kwargs["input_data"], bytes)
            for output in replies:
                with self.subTest(output_type=type(output).__name__):
                    ssh.reset_mock()
                    ssh.return_value = SimpleNamespace(returncode=0, stdout=output)
                    with self.assertRaisesRegex(remote_local_images.RemoteLocalImageError,
                                                "local_images_receipt_publish_unknown") as caught:
                        remote_local_images.publish("target", document,
                                                    entry={"provisioned": True})
                    self.assertEqual(str(caught.exception),
                                     "local_images_receipt_publish_unknown: local image transport failed")
                    self.assertTrue(caught.exception.__suppress_context__ or
                                    caught.exception.__context__ is None)
                    ssh.assert_called_once()
            ssh.return_value = SimpleNamespace(returncode=1, stdout=frame.encode())
            with self.assertRaisesRegex(remote_local_images.RemoteLocalImageError,
                                        "local_images_receipt_publish_unknown"):
                remote_local_images.publish("target", document, entry={"provisioned": True})

    def test_publish_transport_failure_is_unknown_without_exception_disclosure(self):
        for error in [OSError("synthetic private detail"),
                      subprocess.TimeoutExpired("synthetic private detail", 1),
                      subprocess.CalledProcessError(1, "synthetic private detail")]:
            with self.subTest(error=type(error).__name__), \
                 patch.object(remote_local_images._remote, "resolve_sandbox_home",
                              return_value="/synthetic"), \
                 patch.object(remote_local_images._remote, "ssh_run", side_effect=error) as ssh:
                with self.assertRaises(remote_local_images.RemoteLocalImageError) as caught:
                    remote_local_images.publish("target", {"synthetic": "publication"},
                                                entry={"provisioned": True})
                self.assertEqual(caught.exception.code, "local_images_receipt_publish_unknown")
                self.assertNotIn("synthetic private detail", str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
                ssh.assert_called_once()

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

    def test_publication_transport_failure_respects_aggregate_deadline(self):
        publisher = remote_local_images.publish
        for elapsed in (0, 61):
            for error in (OSError("synthetic"), subprocess.TimeoutExpired("synthetic", 1)):
                with self.subTest(elapsed=elapsed, error=type(error).__name__), \
                     self._push_steps(elapsed={"publish": elapsed}) as (calls, _, _entry):
                    advance = remote_local_images.publish

                    def attempt(*args, **kwargs):
                        advance(*args, **kwargs)
                        return publisher(*args, **kwargs)

                    with patch.object(remote_local_images, "publish", side_effect=attempt), \
                         patch.object(remote_local_images._remote, "resolve_sandbox_home",
                                      return_value="/synthetic"), \
                         patch.object(remote_local_images._remote, "ssh_run", side_effect=error) as ssh:
                        expected = ("local_images_transfer_unknown" if elapsed else
                                    "local_images_receipt_publish_unknown")
                        with self.assertRaises(remote_local_images.RemoteLocalImageError) as caught:
                            self._push()
                        self.assertEqual(caught.exception.code, expected)
                        ssh.assert_called_once()
                        self.assertEqual(calls[-1][0], "publish")

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



class SSHReadinessWarningTests(unittest.TestCase):
    def test_stderr_warnings_allow_push_but_stdout_garbage_refuses(self):
        helper=FinalReservationTests()
        missing={'engine':'linux/amd64','images':{}}
        matching=RemoteLocalImageTests._matching_observation()
        for garbage in (False,True):
            with self.subTest(garbage=garbage),tempfile.TemporaryDirectory() as directory, \
                 helper.controller(directory) as (_,_,release,_,_,publish):
                warnings='x'*5000+'\nWarning: Permanently added host\nWARNING: connection is not using a post-quantum key exchange algorithm\nAuthorized users only\n'
                results=[];real_stream=local_images.stream_archive;real_spawn=subprocess.Popen
                def spawn(*args,**kwargs):
                    kwargs['env']=synthetic_environment({'PATH':'/nonexistent'})
                    return real_spawn(*args,**kwargs)
                def stream(_argv,*args,**kwargs):
                    import hashlib
                    frame={**kwargs['resume_frame'],'offset':0,'prefix_sha256':'sha256:'+hashlib.sha256(b'').hexdigest()}
                    script='import sys,json\nsys.stderr.write('+repr(warnings)+');sys.stderr.flush()\n'
                    if garbage:script+="print('garbage on stdout',flush=True)\n"
                    script+='print('+repr(json.dumps(frame))+",flush=True)\nack=json.loads(sys.stdin.buffer.readline())\n"
                    ready={'schema':2,'state':'ready','reservation_id':frame['reservation_id'],'upload_id':frame['upload_id'],'offset':0}
                    script+='print('+repr(json.dumps(ready))+",flush=True)\ndata=sys.stdin.buffer.read()\nprint(json.dumps({'schema':1,'state':'loaded','bytes':len(data)}),flush=True)\n"
                    value=real_stream([sys.executable,'-c',script],*args,**kwargs);results.append(value);return value
                with patch.object(remote_local_images,'observe',side_effect=[missing,missing,matching]), \
                     patch.object(remote_local_images._remote,'ssh_command_args',return_value=['synthetic']), \
                     patch.object(local_images.subprocess,'Popen',side_effect=spawn), \
                     patch.object(local_images,'stream_archive',side_effect=stream):
                    if garbage:
                        with self.assertRaises(remote_local_images.RemoteLocalImageError):helper.push()
                        publish.assert_not_called();release.assert_called_once()
                    else:
                        result=helper.push()
                        self.assertEqual(result['loaded'],['image-1'])
                        self.assertEqual(result['remote_receipt_digest'],'published')
                        publish.assert_called_once();release.assert_not_called()
                self.assertEqual(results[0]['sent_bytes'],0 if garbage else 7)
                self.assertIn('Permanently added',results[0]['stderr_tail'])
                self.assertIn('post-quantum',results[0]['stderr_tail'])
                self.assertLessEqual(len(results[0]['stderr_tail']),4000)
                self.assertNotIn('Warning:',results[0]['output_tail'])

    def test_next_attempt_reclaims_failed_upload_before_admitting_again(self):
        import fcntl
        from sandbox.resources import storage_floor as sf
        from tests.test_hosting_local_images import SharedBundleReceiptTests
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);(home/'deploy-src').mkdir()
            engine=home/'runtime'/'host-local-images';engine.mkdir(parents=True,mode=0o700)
            (engine/'engine.lock').touch(mode=0o600);(engine/'load-fence.lock').touch(mode=0o600)
            store=home/'runtime'/'storage';store.mkdir(mode=0o700)
            ledger=store/'reservations.json'
            peer={'id':'b'*32,'kind':'host-local-images','target':str(home/'deploy-src'/'peer'),
                  'estimate_bytes':300,'expires_at':sf.OPERATION_RESERVATION_EXPIRY}
            ordinary={'id':'ordinary','target':str(home/'deploy-src'/'app'),
                      'estimate_bytes':11,'expires_at':sf.OPERATION_RESERVATION_EXPIRY}
            # The receipt target is app, matching the reservation fixture.
            ledger.write_text(json.dumps([peer,ordinary]));ledger.chmod(0o600)
            receipt=SharedBundleReceiptTests.receipt()
            events=[];tokens=[];admissions=[];reconciles=[0]
            @contextmanager
            def lease(*args,**kwargs):
                with (engine/'engine.lock').open('r+b') as lock:
                    fcntl.flock(lock,fcntl.LOCK_EX)
                    yield SimpleNamespace(require=lambda:None,lost=lambda:False)
            def ssh(entry,command,timeout=30,**kwargs):
                if command=="docker info --format '{{.ID}}'":return SimpleNamespace(returncode=0,stdout='same-daemon')
                if remote_local_images._RECONCILE_PROGRAM in shlex.split(command):
                    reconciles[0]+=1
                    if reconciles[0]==2:raise OSError('connection lost after failed upload')
                    events.append('reconcile')
                argv=shlex.split(command)
                self.assertEqual(argv[:2],['python3','-c'])
                prefix='import os\nfrom types import SimpleNamespace\nos.statvfs=lambda _:SimpleNamespace(f_bavail=10**12,f_frsize=1)\n'
                return run_test_process([sys.executable,'-c',prefix+argv[2],*argv[3:]],
                    capture_output=True,text=True,timeout=3,env={'PATH':'/nonexistent','SANDBOX_HOME':directory})
            actual_admit=sf.remote_storage_floor_admission
            def admit(*args,**kwargs):
                events.append('admit')
                decision=actual_admit(*args,**kwargs);admissions.append(decision);return decision
            def upload(*args,**kwargs):
                token=kwargs['reservation_id'];tokens.append(token);kwargs['on_started']()
                if len(tokens)==1:raise remote_local_images.RemoteLocalImageError('local_images_transfer_unknown')
                self.assertNotIn(tokens[0],[e['id'] for e in json.loads(ledger.read_text())])
                sf.release_storage_reservation(str(store),token)
                return {'schema':1,'state':'loaded','bytes':10}
            missing={'engine':'linux/amd64','images':{}};matching=RemoteLocalImageTests._matching_observation()
            with patch.object(local_images,'load_receipt',return_value=(receipt,home/'receipt.json')), \
                 patch.object(local_images,'bundle_identities'), \
                 patch.object(remote_local_images,'target_image_lease',side_effect=lease), \
                 patch.object(remote_local_images._remote,'resolve_sandbox_home',return_value=directory), \
                 patch.object(remote_local_images._remote,'ssh_run',side_effect=ssh), \
                 patch.object(sf,'ssh_run',side_effect=ssh), \
                 patch.object(sf,'remote_storage_floor_admission',side_effect=admit), \
                 patch.object(remote_local_images,'load_archive',side_effect=upload), \
                 patch.object(remote_local_images,'observe',side_effect=[missing,missing,missing,missing,matching]), \
                 patch.object(remote_local_images,'publish',return_value='published'):
                with self.assertRaises(remote_local_images.RemoteLocalImageError):FinalReservationTests.push()
                self.assertIn(tokens[0],[e['id'] for e in json.loads(ledger.read_text())])
                self.assertFalse((engine/'load-unverified').exists())
                result=FinalReservationTests.push()
            self.assertEqual(result['remote_receipt_digest'],'published')
            self.assertEqual(events,['reconcile','admit','reconcile','admit'])
            self.assertEqual([d['reserved_bytes'] for d in admissions],[311,311])
            self.assertEqual(json.loads(ledger.read_text()),[peer,ordinary])


if __name__ == "__main__":
    unittest.main()

class StorageInspectionTests(unittest.TestCase):
    def test_inspection_uses_full_v2_bundle_and_never_reserves_or_observes_docker(self):
        from tests.test_hosting_local_images import SharedBundleReceiptTests
        receipt = SharedBundleReceiptTests.receipt()
        receipt['bundles']['bundle-1']['archive_bytes'] = 100_000_000
        receipt = local_images.seal_receipt(receipt)
        with patch.object(remote_local_images, '_entry', return_value={'storage_floor_gb': 30}), \
             patch.object(remote_local_images._remote, 'resolve_sandbox_home', return_value='/srv/sandbox'), \
             patch.object(local_images, 'load_receipt', return_value=(receipt, Path('/receipt'))), \
             patch('sandbox.resources.storage_floor.remote_storage_floor_inspection', return_value={'ok': True}) as inspect, \
             patch('sandbox.resources.storage_floor.remote_storage_floor_admission') as reserve, \
             patch.object(remote_local_images, 'observe') as observe:
            self.assertEqual(remote_local_images.inspect_storage('remote', receipt_path_value='/receipt'), {'ok': True})
            self.assertEqual(inspect.call_args.kwargs['archive_bytes'], 100_000_000)
            self.assertEqual(inspect.call_args.kwargs['multiplier'], 3)
            reserve.assert_not_called()
            observe.assert_not_called()

    def test_mismatched_receipt_target_refuses_without_probe(self):
        with patch.object(remote_local_images, '_entry', return_value={}), \
             patch.object(local_images, 'load_receipt', return_value=(_receipt(), Path('/receipt'))), \
             patch('sandbox.resources.storage_floor.remote_storage_floor_inspection') as probe:
            with self.assertRaises(remote_local_images.RemoteLocalImageError):
                remote_local_images.inspect_storage('remote', receipt_path_value='/receipt', project='foreign')
            probe.assert_not_called()

    def test_public_command_wiring_is_read_only_and_renders_report(self):
        args = SimpleNamespace(action='image', image_action='storage-admission', remote='target',
            project='lenzora', environment='development', json=True, receipt=None, confirm=False)
        with patch.object(remote_local_images, 'inspect_storage', return_value={'ok': True, 'reservation_created': False}) as inspect, redirect_stdout(io.StringIO()) as out:
            hosting_cmd.cmd_host({}, args)
        self.assertFalse(json.loads(out.getvalue())['storage']['reservation_created'])
        inspect.assert_called_once_with('target', receipt_path_value=None, project='lenzora', environment='development')

class StorageInspectionRoundThreeTests(unittest.TestCase):
    @staticmethod
    def args(as_json=True, receipt=None):
        return SimpleNamespace(action='image', image_action='storage-admission', remote='fixture',
            project='app', environment='development', json=as_json, receipt=receipt, confirm=False)

    def test_command_bounds_actual_home_resolution_failures_without_mocking_inspection(self):
        cases = [SimpleNamespace(returncode=255, stdout='', stderr='SB_SYNTHETIC_PRIVATE /forbidden'),
                 RuntimeError('SB_SYNTHETIC_PRIVATE /forbidden'),
                 FileNotFoundError('SB_SYNTHETIC_PRIVATE /forbidden'),
                 subprocess.TimeoutExpired('SB_SYNTHETIC_PRIVATE', 15),
                 remote_local_images._remote.RemoteHomeResolutionTimeout(15),
                 SimpleNamespace(returncode=0, stdout='/a/../SB_SYNTHETIC_PRIVATE\n')]
        for failure in cases:
            for as_json in (True, False):
                with self.subTest(failure=type(failure).__name__, json=as_json), \
                     patch.object(remote_local_images, '_entry', return_value={'provisioned': True, 'ssh': 'fixture'}), \
                     patch('sandbox.resources.storage_floor._run_probe', side_effect=AssertionError('failed home must not probe')), \
                     patch.object(remote_local_images._remote, 'ssh_run', **({'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure})) as ssh, \
                     redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                    with self.assertRaises(SystemExit) as exited:
                        hosting_cmd.cmd_host({}, self.args(as_json))
                    self.assertEqual(exited.exception.code, 1)
                    ssh.assert_called_once()
                    output = out.getvalue()
                    self.assertIn('storage_inspection_unavailable', output)
                    self.assertIn('no reservation created', output)
                    self.assertNotIn('SB_SYNTHETIC_PRIVATE', output + err.getvalue())
                    self.assertNotIn('Traceback', output + err.getvalue())
                    if as_json:
                        self.assertEqual(json.loads(output), {'ok': False, 'code': 'storage_inspection_unavailable',
                            'message': 'storage admission evidence unavailable; no reservation created'})
                    else:
                        self.assertIn('Storage admission: blocked', output)

    def test_v1_without_floor_skips_the_same_admission_as_push(self):
        for floor in (None, 0):
            entry = {'storage_floor_gb': floor}
            with self.subTest(floor=floor), patch.object(remote_local_images, '_entry', return_value=entry), \
                 patch.object(local_images, 'load_receipt', return_value=(_receipt(), Path('/receipt'))), \
                 patch.object(remote_local_images._remote, 'ssh_run', side_effect=AssertionError('no SSH needed')), \
                 patch('sandbox.resources.storage_floor._run_probe', side_effect=AssertionError('no probe needed')):
                report = remote_local_images.inspect_storage('fixture', receipt_path_value='/receipt')
                self.assertTrue(report['ok'])
                self.assertFalse(report['floor_applied'])
                self.assertEqual(report['reason'], 'floor_not_configured')
                self.assertEqual(report['floor_bytes'], 0)
                self.assertIsNone(report['snapshot_at'])
                self.assertIsNone(remote_local_images._storage_admission(entry, _receipt(), {'images': {}}))
                with redirect_stdout(io.StringIO()) as out:
                    hosting_cmd.cmd_host({}, self.args(False, '/receipt'))
                self.assertIn('Storage admission: not required; reason: floor_not_configured', out.getvalue())

    def test_text_verdict_and_safe_specific_snapshot_reason(self):
        from sandbox.resources import storage_floor as sf
        cases = [({'status': 'unavailable', 'reason': 'storage_snapshot_changed'}, 'storage_snapshot_changed'),
                 ({'status': 'complete', 'free_bytes': 0, 'reserved_bytes': 0, 'estimate_bytes': 1,
                   'estimate_source': 'minimum', 'live_reservations': 0, 'snapshot_at': 12345.0}, 'below_floor')]
        for payload, reason in cases:
            with self.subTest(reason=reason), patch.object(remote_local_images, '_entry', return_value={'storage_floor_gb': 1}), \
                 patch.object(remote_local_images._remote, 'resolve_sandbox_home', return_value='/srv/sandbox'), \
                 patch.object(sf, '_run_probe', return_value=payload), redirect_stdout(io.StringIO()) as out:
                with self.assertRaises(SystemExit):
                    hosting_cmd.cmd_host({}, self.args(False))
                self.assertIn('Storage admission: blocked; reason: ' + reason, out.getvalue())
                self.assertNotIn('None bytes', out.getvalue())

class ResumableStreamTests(unittest.TestCase):
    def test_verified_prefix_is_not_sent_again(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory, 'bundle.tar')
            archive.write_bytes(b'prefix-suffix')
            prefix = 'sha256:' + hashlib.sha256(b'prefix-').hexdigest()
            base = {'schema': 2, 'state': 'resume', 'reservation_id': 'a'*32,
                    'upload_id': 'b'*64, 'size': 13}
            worker = """
import json,sys
base=json.loads(sys.argv[1]);prefix=sys.argv[2]
print(json.dumps(dict(base,offset=7,prefix_sha256=prefix)),flush=True)
ack=json.loads(sys.stdin.buffer.readline())
assert ack=={'schema':2,'action':'resume','offset':7,'prefix_sha256':prefix},ack
print(json.dumps({'schema':2,'state':'ready','reservation_id':base['reservation_id'],'upload_id':base['upload_id'],'offset':7}),flush=True)
raw=sys.stdin.buffer.read()
assert raw==b'suffix',raw
print(json.dumps({'schema':1,'state':'loaded','bytes':13}))
"""
            real_spawn = subprocess.Popen
            def spawn(*args, **kwargs):
                kwargs['env'] = synthetic_environment()
                return real_spawn(*args, **kwargs)
            with patch.object(local_images.subprocess, 'Popen', side_effect=spawn):
                result = local_images.stream_archive([sys.executable, '-c', worker, json.dumps(base), prefix], archive,
                    timeout=3, resume_frame=base)
            self.assertEqual(result['returncode'], 0, result['output_tail'])
            self.assertEqual(result['sent_bytes'], 6)
            self.assertEqual(result['resumed_bytes'], 7)
            self.assertEqual(result['sha256'], 'sha256:' + hashlib.sha256(archive.read_bytes()).hexdigest())

class ResumableWorkerTests(unittest.TestCase):
    def setUp(self):
        import hashlib
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / 'runtime/host-local-images'
        self.root.mkdir(parents=True, mode=0o700)
        self.archive = self.home / 'bundle-1.tar'
        self.archive.write_bytes(b'abcdefgh' * (1024 * 1024))
        self.archive.chmod(0o600)
        self.want = 'sha256:' + hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.context = {'schema': 1, 'project': 'app', 'environment': 'development',
                        'machine_identity': 'a'*24, 'registered_host_digest': 'sha256:'+'b'*64,
                        'receipt_digest': 'sha256:'+'c'*64, 'bundle_digest': self.want,
                        'archive_bytes': self.archive.stat().st_size}
        self.token = 'd'*32
        self.bin = self.home / 'bin'; self.bin.mkdir()
        self.loaded = self.home / 'loaded'
        (self.bin / 'docker').write_text('#!'+sys.executable+'\nimport pathlib,sys\npathlib.Path('+repr(str(self.loaded))+').write_text(str(pathlib.Path(sys.argv[-1]).stat().st_size))\n')
        (self.bin / 'docker').chmod(0o700)
        self.child_env = {'PATH': str(self.bin)+':/usr/bin:/bin', 'TMPDIR': str(self.home), 'PYTHONDONTWRITEBYTECODE': '1'}
        self.reservation()

    def reservation(self):
        store = self.home / 'runtime/storage'; store.mkdir(mode=0o700, exist_ok=True)
        ledger = store / 'reservations.json'
        ledger.write_text(json.dumps([{'id':self.token,'kind':'host-local-images','target':str(self.home/'deploy-src/app')}]))
        ledger.chmod(0o600)

    def command(self):
        return [sys.executable, '-c', remote_local_images._RESUMABLE_STAGE_PROGRAM,
                str(self.root/'uploads'), json.dumps(self.context), self.token, '5']

    def start(self):
        child = subprocess.Popen(self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=synthetic_environment(self.child_env))
        self.addCleanup(self.stop, child)
        return child

    @staticmethod
    def stop(child):
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)

    @staticmethod
    def offer(child):
        import selectors
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            if not selector.select(3):
                raise AssertionError('no bounded offer from local worker')
        return json.loads(child.stdout.readline())

    def partial(self, content=b'abcdefgh'):
        child = self.start(); offer = self.offer(child)
        child.stdin.write((json.dumps({'schema':2,'action':'resume','offset':0,'prefix_sha256':offer['prefix_sha256']})+'\n').encode())
        child.stdin.flush(); self.assertEqual(self.offer(child)['state'], 'ready')
        child.stdin.write(content);child.stdin.close();child.stdin=None
        output, error = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 32, error)
        paths = list((self.root/'uploads').glob('.resume-*.tar'))
        self.assertEqual(len(paths),1)
        self.assertEqual(paths[0].read_bytes(), content)
        self.assertFalse(self.loaded.exists())
        self.reservation()
        return paths[0]

    def transfer(self):
        real_spawn = subprocess.Popen
        def spawn(*args, **kwargs):
            kwargs['env'] = synthetic_environment(self.child_env)
            return real_spawn(*args, **kwargs)
        artifact = {'archive':self.archive.name,'archive_sha256':self.want,
                    'archive_bytes':self.archive.stat().st_size,'members':['image-1']}
        with patch.object(remote_local_images._remote,'resolve_sandbox_home',return_value=str(self.home)), \
             patch.object(remote_local_images._remote,'ssh_command_args',return_value=self.command()), \
             patch.object(local_images.subprocess,'Popen',side_effect=spawn):
            return remote_local_images.load_archive('local',self.archive,artifact,timeout=10,entry={},
                reservation_id=self.token,resume_context=self.context)

    def test_connection_drop_keeps_prefix_and_rerun_sends_only_suffix(self):
        self.partial()
        frame = self.transfer()
        self.assertEqual(frame['resumed_bytes'],8)
        self.assertEqual(self.loaded.read_text(),str(self.archive.stat().st_size))
        self.assertEqual(list((self.root/'uploads').glob('.resume-*')),[])

    def test_corrupted_prefix_is_discarded_before_suffix_can_be_sent(self):
        path = self.partial();path.write_bytes(b'corrupt!')
        self.assertEqual(self.transfer()['resumed_bytes'],0)
        self.assertTrue(self.loaded.exists())

    def test_two_controllers_cannot_append_concurrently(self):
        first = self.start(); self.offer(first)
        second = self.start(); out, error = second.communicate(timeout=3)
        self.assertEqual(second.returncode,75,error)
        self.assertEqual(out,b'')
        self.assertFalse(self.loaded.exists())

    def test_killed_controller_leaves_reusable_bytes_and_releases_fences(self):
        import signal,time,fcntl
        harness = """
import json,os,signal,sys
from pathlib import Path
from unittest.mock import patch
from sandbox.transports import remote_local_images as transport
ctx=json.loads(sys.argv[3]);home=Path(sys.argv[1]);archive=Path(sys.argv[2])
artifact={'archive':archive.name,'archive_sha256':ctx['bundle_digest'],'archive_bytes':ctx['archive_bytes'],'members':['image-1']}
def command(entry, command, **kwargs):
 import shlex
 argv=shlex.split(command);argv[0]=sys.executable;return argv
def kill_after_chunk(message):
 if 'MiB (' in message: os.kill(os.getpid(),signal.SIGKILL)
with patch.object(transport._remote,'resolve_sandbox_home',return_value=str(home)),patch.object(transport._remote,'ssh_command_args',side_effect=command):
 with transport.target_image_lease('local','app','development',ctx['receipt_digest'],entry={}) as lease:
  transport.load_archive('local',archive,artifact,timeout=10,entry={},reservation_id=sys.argv[4],resume_context=ctx,progress=kill_after_chunk,cancelled=lease.lost)
"""
        result=run_test_process([sys.executable,'-c',harness,str(self.home),str(self.archive),json.dumps(self.context),self.token],env=self.child_env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,-signal.SIGKILL,result.stderr)
        deadline=time.monotonic()+5
        while True:
            with open(self.root/'load-fence.lock','rb') as handle:
                try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:
                    if time.monotonic()>=deadline:raise AssertionError('worker survived EOF')
                    time.sleep(.01);continue
                break
        paths=list((self.root/'uploads').glob('.resume-*.tar'))
        self.assertEqual(len(paths),1)
        saved=paths[0].stat().st_size
        self.assertGreater(saved,0);self.assertLess(saved,self.archive.stat().st_size)
        self.reservation()
        self.assertEqual(self.transfer()['resumed_bytes'],saved)
        self.assertTrue(self.loaded.exists())

    def test_busy_worker_does_not_release_another_controllers_reservation(self):
        first=self.start();self.offer(first)
        second=self.start();second.communicate(timeout=3)
        self.assertEqual(second.returncode,75)
        ledger=json.loads((self.home/'runtime/storage/reservations.json').read_text())
        self.assertEqual([row['id'] for row in ledger],[self.token])
        engine=run_test_process([sys.executable,'-c',remote_local_images._ENGINE_LOCK_PROGRAM,str(self.root)],input=b'',capture_output=True,timeout=3)
        self.assertEqual(engine.returncode,75,engine.stderr)
        self.assertEqual(json.loads(engine.stdout),{'schema':1,'acquired':False})

    def test_matching_full_partial_loads_without_resending_bytes(self):
        self.partial(self.archive.read_bytes()[:-1])
        paths=list((self.root/'uploads').glob('.resume-*.tar'))
        paths[0].write_bytes(self.archive.read_bytes())
        self.assertEqual(self.transfer()['resumed_bytes'],self.archive.stat().st_size)
        self.assertTrue(self.loaded.exists())

    def test_new_receipt_discards_same_scope_old_bundle_partial(self):
        old=self.partial()
        self.context['receipt_digest']='sha256:'+'e'*64
        self.assertEqual(self.transfer()['resumed_bytes'],0)
        self.assertFalse(old.exists())

    def test_same_receipt_cannot_reuse_partial_from_another_machine(self):
        old=self.partial()
        self.context['machine_identity']='e'*24
        self.assertEqual(self.transfer()['resumed_bytes'],0)
        self.assertTrue(old.exists())  # Foreign identity is not silently deleted.

    def test_missing_reservation_sends_no_offer_and_loads_nothing(self):
        (self.home/'runtime/storage/reservations.json').write_text('[]')
        child=self.start();output,_=child.communicate(timeout=3)
        self.assertNotEqual(child.returncode,0)
        self.assertEqual(output,b'');self.assertFalse(self.loaded.exists())

    def test_symlink_and_hardlink_partial_are_not_followed_or_removed(self):
        import os
        for kind in ('symlink','hardlink'):
            with self.subTest(kind=kind):
                path=self.partial();path.unlink()
                victim=self.home/'victim';victim.write_bytes(b'untouched');victim.chmod(0o600)
                if kind=='symlink':path.symlink_to(victim)
                else:os.link(victim,path)
                child=self.start();child.communicate(timeout=3)
                self.assertNotEqual(child.returncode,0)
                self.assertEqual(victim.read_bytes(),b'untouched')
                self.assertTrue(path.exists());path.unlink();victim.unlink()
                self.reservation()

    def test_stale_partial_is_reaped_under_fence_before_reservation_admission(self):
        import os,time
        path=self.partial()
        for item in (self.root/'uploads').iterdir():os.utime(item,(time.time()-90000,)*2)
        program=remote_local_images._RECONCILE_PROGRAM
        result=run_test_process([sys.executable,'-c',program,str(self.root),str(self.home/'deploy-src/app')],capture_output=True,text=True,timeout=3)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),{'safe':True})
        self.assertFalse(path.exists())
        self.reservation()
        self.assertEqual(self.transfer()['resumed_bytes'],0)

    def test_quarantine_blocks_gc_and_preserves_load_evidence(self):
        import os,time
        path=self.partial()
        for item in (self.root/'uploads').iterdir():os.utime(item,(time.time()-90000,)*2)
        (self.root/'load-unverified').write_text('{}')
        result=run_test_process([sys.executable,'-c',remote_local_images._RECONCILE_PROGRAM,str(self.root),str(self.home/'deploy-src/app')],capture_output=True,text=True,timeout=3)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),{'safe':False})
        self.assertTrue(path.exists())

    def test_failed_load_retains_full_archive_reservation_and_quarantine(self):
        (self.bin/'docker').write_text('#!'+sys.executable+'\nraise SystemExit(1)\n')
        with self.assertRaisesRegex(remote_local_images.RemoteLocalImageError,'local_images_transfer_unknown'):
            self.transfer()
        self.assertTrue((self.root/'load-unverified').exists())
        self.assertEqual((self.root/'load-fence.lock').stat().st_mode&0o777,0o400)
        self.assertEqual(len(list((self.root/'uploads').glob('.resume-*.tar'))),1)
        self.assertEqual(len(json.loads((self.home/'runtime/storage/reservations.json').read_text())),1)

    def test_old_worker_handshake_refuses_clearly_without_sending_archive(self):
        legacy="import json,sys;print(json.dumps({'schema':1,'state':'ready','reservation_id':sys.argv[1]}),flush=True);assert not sys.stdin.buffer.read()"
        real_spawn=subprocess.Popen
        def spawn(*args,**kwargs):
            kwargs['env']=synthetic_environment(self.child_env);return real_spawn(*args,**kwargs)
        artifact={'archive':self.archive.name,'archive_sha256':self.want,'archive_bytes':self.archive.stat().st_size,'members':['image-1']}
        with patch.object(remote_local_images._remote,'resolve_sandbox_home',return_value=str(self.home)), \
             patch.object(remote_local_images._remote,'ssh_command_args',return_value=[sys.executable,'-c',legacy,self.token]), \
             patch.object(local_images.subprocess,'Popen',side_effect=spawn):
            with self.assertRaisesRegex(remote_local_images.RemoteLocalImageError,'local_images_resume_protocol_unsupported'):
                remote_local_images.load_archive('local',self.archive,artifact,timeout=3,entry={},reservation_id=self.token,resume_context=self.context)

    def test_old_runtime_identity_failure_is_a_clear_pretransfer_refusal(self):
        from sandbox.resources.host_memory.remote import RemoteProtocolError
        args=SimpleNamespace(confirm=True,remote='target',receipt='/synthetic/receipt.json',timeout=60,artifact_timeout=30,json=True)
        with ExitStack() as stack:
            stack.enter_context(patch.object(hosting_cmd.remote,'registered_remote_lock'))
            stack.enter_context(patch.object(hosting_cmd.remote,'registration_lock_name',return_value='fixture'))
            stack.enter_context(patch.object(remote_local_images,'_entry',return_value={'mcp_service':{'runtime_revision':'a'*24}}))
            stack.enter_context(patch('sandbox.readiness.gate.require_ready'))
            stack.enter_context(patch.object(hosting_cmd.remote,'resolve_sandbox_home',return_value='/synthetic'))
            stack.enter_context(patch.object(hosting_cmd.remote,'_remote_mcp_runtime_revision',return_value='b'*24))
            stack.enter_context(patch.object(hosting_cmd,'_authenticated_machine_identity',side_effect=RemoteProtocolError('remote_runtime_revision_mismatch','secret-canary')))
            push=stack.enter_context(patch.object(remote_local_images,'push_receipt'))
            output=io.StringIO()
            with redirect_stdout(output),self.assertRaises(SystemExit):hosting_cmd._cmd_host_image_push_local(args)
            value=json.loads(output.getvalue())
            self.assertEqual(value['code'],'remote_runtime_revision_mismatch')
            for value_part in ['a'*24,'b'*24,'live unknown','before archive transfer','remote service status target --json','owner approval']:
                self.assertIn(value_part,value['message'])
            self.assertNotIn('secret-canary',output.getvalue());push.assert_not_called()

    def test_cache_capacity_refuses_before_creating_an_extra_pair(self):
        uploads=self.root/'uploads';uploads.mkdir(mode=0o700)
        for index in range(127):
            path=uploads/('unrecognized-'+str(index));path.touch(mode=0o600)
        with self.assertRaisesRegex(remote_local_images.RemoteLocalImageError,'local_images_partial_cache_full'):
            self.transfer()
        self.assertEqual(len(list(uploads.iterdir())),127)
        self.assertFalse(self.loaded.exists())

    def test_corrupt_metadata_never_authorizes_append_and_retry_recreates_pair(self):
        self.partial()
        meta=next((self.root/'uploads').glob('.resume-*.json'))
        meta.write_text('{"bad":true}')
        with self.assertRaisesRegex(remote_local_images.RemoteLocalImageError,'local_images_partial_metadata_invalid'):
            self.transfer()
        self.assertFalse(self.loaded.exists())
        self.reservation()
        self.assertEqual(self.transfer()['resumed_bytes'],0)
