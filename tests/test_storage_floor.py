import json
import unittest
from types import SimpleNamespace
from unittest import mock

from sandbox.resources import storage_floor as sf

GB = 1024 ** 3


def probe(free, reserved=0, estimate=None, *, status="complete", admitted=None):
    payload = {"status": status, "free_bytes": free, "reserved_bytes": reserved,
               "history_bytes": estimate}
    if admitted is not None:
        payload["admitted"] = admitted
    return payload


class EvaluateTest(unittest.TestCase):
    def test_admits_within_budget(self):
        decision = sf.evaluate_storage_floor(probe(80 * GB, 0, 3 * GB), floor_gb=30)
        self.assertTrue(decision["ok"])
        self.assertEqual(decision["estimate_source"], "history")
        self.assertEqual(decision["estimate_bytes"], 3 * GB)

    def test_refuses_below_floor(self):
        decision = sf.evaluate_storage_floor(probe(32 * GB, 0, 3 * GB), floor_gb=30)
        self.assertFalse(decision["ok"])
        self.assertEqual(decision["code"], "remote_storage_floor")
        self.assertEqual(decision["reason"], "below_floor")
        self.assertEqual(decision["floor_bytes"], 30 * GB)

    def test_counts_reservations(self):
        decision = sf.evaluate_storage_floor(probe(35 * GB, 3 * GB, 3 * GB), floor_gb=30)
        self.assertFalse(decision["ok"])
        self.assertEqual(decision["reserved_bytes"], 3 * GB)

    def test_equal_to_floor_is_admitted(self):
        decision = sf.evaluate_storage_floor(probe(33 * GB, 0, 3 * GB), floor_gb=30)
        self.assertTrue(decision["ok"])

    def test_default_estimate_is_flagged(self):
        decision = sf.evaluate_storage_floor(probe(80 * GB, 0, None), floor_gb=30)
        self.assertTrue(decision["ok"])
        self.assertEqual(decision["estimate_source"], "default")
        self.assertEqual(decision["estimate_bytes"], sf.DEFAULT_ESTIMATE_BYTES)

    def test_explicit_minimum_estimate_is_authoritative(self):
        payload = probe(80 * GB, 0, 3 * GB)
        payload.update(estimate_bytes=12 * GB, estimate_source="minimum")
        decision = sf.evaluate_storage_floor(payload, floor_gb=30)
        self.assertTrue(decision["ok"])
        self.assertEqual(decision["estimate_bytes"], 12 * GB)
        self.assertEqual(decision["estimate_source"], "minimum")

    def test_unknown_measurement_refuses(self):
        for payload in ({"status": "unavailable"}, None, {"status": "complete"},
                        probe(-1, 0, 1), probe(True, 0, 1)):
            decision = sf.evaluate_storage_floor(payload, floor_gb=30)
            self.assertFalse(decision["ok"], payload)
            self.assertEqual(decision["reason"], "measurement_unavailable")

    def test_remote_refusal_is_authoritative(self):
        # The remote decides under its lock; a remote "not admitted" wins even
        # when the local arithmetic would pass with stale numbers.
        decision = sf.evaluate_storage_floor(probe(80 * GB, 0, 3 * GB, admitted=False),
                                             floor_gb=30)
        self.assertFalse(decision["ok"])

    def test_floor_bytes_helper(self):
        self.assertIsNone(sf.floor_bytes({}))
        self.assertIsNone(sf.floor_bytes({"storage_floor_gb": 0}))
        self.assertIsNone(sf.floor_bytes({"storage_floor_gb": "x"}))
        self.assertIsNone(sf.floor_bytes({"storage_floor_gb": True}))
        self.assertEqual(sf.floor_bytes({"storage_floor_gb": 30}), 30 * GB)


class AdmissionTest(unittest.TestCase):
    def test_no_floor_skips_probe(self):
        with mock.patch.object(sf, "ssh_run") as ssh:
            self.assertIsNone(sf.remote_storage_floor_admission({}, "/h/deploy-src/x"))
        ssh.assert_not_called()

    def test_probe_output_parsed_and_floor_sent(self):
        out = json.dumps(probe(80 * GB, 0, 3 * GB, admitted=True))
        with mock.patch.object(sf, "ssh_run", return_value=SimpleNamespace(
                returncode=0, stdout="noise\n" + out + "\n")) as ssh:
            decision = sf.remote_storage_floor_admission(
                {"storage_floor_gb": 30}, "/h/deploy-src/x")
        self.assertTrue(decision["ok"])
        command = ssh.call_args.args[1]
        self.assertIn("python3 -c", command)

    def test_ssh_failure_refuses(self):
        with mock.patch.object(sf, "ssh_run", side_effect=OSError("down")):
            decision = sf.remote_storage_floor_admission(
                {"storage_floor_gb": 30}, "/h/deploy-src/x")
        self.assertFalse(decision["ok"])
        self.assertEqual(decision["reason"], "measurement_unavailable")

    def test_ambiguous_output_refuses(self):
        out = json.dumps(probe(80 * GB)) + "\n" + json.dumps(probe(80 * GB))
        with mock.patch.object(sf, "ssh_run", return_value=SimpleNamespace(
                returncode=0, stdout=out)):
            decision = sf.remote_storage_floor_admission(
                {"storage_floor_gb": 30}, "/h/deploy-src/x")
        self.assertFalse(decision["ok"])


class RemoteProgramTest(unittest.TestCase):
    """Run the shipped program locally against a temp deploy-src."""

    def run_program(self, root, floor, estimate_hint=None, now=None):
        import io, contextlib
        request = {"target": str(root / "deploy-src" / "proj"), "floor_bytes": floor,
                   "default_estimate": sf.DEFAULT_ESTIMATE_BYTES,
                   "window_seconds": sf.RESERVATION_WINDOW_SECONDS,
                   "reservations": str(root / "runtime" / "storage")}
        buffer = io.StringIO()
        scope = {"REQUEST": request, "__name__": "__sb_storage_floor__"}
        with contextlib.redirect_stdout(buffer):
            exec(sf._PROGRAM, scope)
        return json.loads(buffer.getvalue().strip().splitlines()[-1])

    def test_reserves_and_second_admission_sees_it(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "deploy-src" / "proj-workspace-a").mkdir(parents=True)
            (root / "deploy-src" / "proj-workspace-a" / "f").write_bytes(b"x" * 8192)
            first = self.run_program(root, 1)
            self.assertEqual(first["status"], "complete")
            self.assertTrue(first["admitted"])
            self.assertGreater(first["history_bytes"], 0)
            second = self.run_program(root, 1)
            self.assertEqual(second["reserved_bytes"], first["history_bytes"])
            stored = json.loads((root / "runtime" / "storage" / "reservations.json").read_text())
            self.assertEqual(len(stored), 2)

    def test_refusal_does_not_reserve(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "deploy-src").mkdir()
            result = self.run_program(root, 10 ** 18)
            self.assertFalse(result["admitted"])
            self.assertFalse((root / "runtime" / "storage" / "reservations.json").exists())



class WiringTest(unittest.TestCase):
    def test_deploy_refuses_before_any_remote_write(self):
        import tempfile
        from sandbox.core import _remote
        refused = {"ok": False, "code": "remote_storage_floor", "reason": "below_floor",
                   "free_bytes": 1, "reserved_bytes": 0, "estimate_bytes": 1,
                   "floor_bytes": 30 * GB, "estimate_source": "default"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(_remote, "capture_uncommitted", return_value=("", [])), \
                mock.patch.object(_remote, "snapshot_dirty_overlay", return_value={"digest": "d"}), \
                mock.patch.object(_remote, "remote_network_capacity_admission",
                                  return_value={"ok": True}), \
                mock.patch.object(_remote, "deploy_target_path", return_value="/h/deploy-src/p"), \
                mock.patch.object(sf, "remote_storage_floor_admission",
                                  return_value=refused) as gate, \
                mock.patch.object(_remote, "ensure_deploy_repo") as ensure:
            with self.assertRaises(sf.StorageFloorAdmissionError):
                _remote.deploy_exact_working_tree({"storage_floor_gb": 30}, tmp)
        gate.assert_called_once()
        ensure.assert_not_called()

    def test_no_floor_never_probes(self):
        import tempfile
        from sandbox.core import _remote
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(_remote, "capture_uncommitted", return_value=("", [])), \
                mock.patch.object(_remote, "snapshot_dirty_overlay", return_value={"digest": "d"}), \
                mock.patch.object(_remote, "remote_network_capacity_admission",
                                  return_value={"ok": True}), \
                mock.patch.object(sf, "remote_storage_floor_admission") as gate, \
                mock.patch.object(_remote, "ensure_deploy_repo",
                                  side_effect=RuntimeError("stop")):
            with self.assertRaises(RuntimeError):
                _remote.deploy_exact_working_tree({}, tmp)
        gate.assert_not_called()

    def test_transport_payload(self):
        from sandbox.transports.remote_jobs import (
            RemoteJobAdmissionError, RemoteJobStorageFloorError)
        error = RemoteJobStorageFloorError({
            "reason": "below_floor", "free_bytes": 32 * GB, "reserved_bytes": 0,
            "estimate_bytes": 3 * GB, "floor_bytes": 30 * GB,
            "estimate_source": "history", "secret": "x"})
        self.assertIsInstance(error, RemoteJobAdmissionError)
        payload = error.to_payload(remote="xcloud-london")
        self.assertEqual(payload["code"], "remote_storage_floor")
        self.assertEqual(payload["storage"]["free_bytes"], 32 * GB)
        self.assertEqual(payload["target"]["remote"], "xcloud-london")
        self.assertNotIn("secret", json.dumps(payload))
        self.assertIn("32.0 GB", payload["detail"])

class SafetyRegressionTest(RemoteProgramTest):
    def test_corrupt_reservations_refuse_without_overwrite(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / 'runtime' / 'storage'
            store.mkdir(parents=True)
            ledger = store / 'reservations.json'
            ledger.write_text('broken')
            result = self.run_program(root, 1)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(ledger.read_text(), 'broken')

    def test_refusal_preserves_expired_ledger(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / 'runtime' / 'storage'
            store.mkdir(parents=True)
            ledger = store / 'reservations.json'
            previous = json.dumps([{'id':'old','estimate_bytes':1,'expires_at':1}])
            ledger.write_text(previous)
            self.assertFalse(self.run_program(root, 10 ** 18)['admitted'])
            self.assertEqual(ledger.read_text(), previous)

    def test_largest_recent_workspace_drives_estimate(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for suffix in ('a','b','c'):
                (root / 'deploy-src' / ('proj-workspace-' + suffix)).mkdir(parents=True)
            with mock.patch('subprocess.run', side_effect=[
                    SimpleNamespace(returncode=0, stdout='100\tx'),
                    SimpleNamespace(returncode=0, stdout='200\tx'),
                    SimpleNamespace(returncode=0, stdout='9000\tx')]):
                result = self.run_program(root, 1)
            self.assertEqual(result['history_bytes'], 9000 * 1024)

    def test_one_unmeasurable_filesystem_refuses(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch('os.path.isdir', return_value=True), \
                    mock.patch('os.statvfs', side_effect=[SimpleNamespace(f_bavail=100,f_frsize=GB), OSError('unknown')]):
                self.assertEqual(self.run_program(Path(tmp),1)['status'], 'unavailable')

    def test_malformed_configured_floor_fails_closed(self):
        for value in ('30', True, -1):
            with mock.patch.object(sf, 'ssh_run') as ssh:
                decision = sf.remote_storage_floor_admission({'storage_floor_gb':value}, '/h/deploy-src/p')
            self.assertIsInstance(decision, dict)
            self.assertFalse(decision['ok'])
            ssh.assert_not_called()

class CoordinationTest(RemoteProgramTest):
    def test_admission_refuses_while_reclaim_guard_held(self):
        import fcntl, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'deploy-src').mkdir()
            guard = root/'runtime'/'resources'/'reclaim-host.lock'
            guard.parent.mkdir(parents=True)
            guard.touch(mode=0o600)
            with guard.open('r+') as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = self.run_program(root, 1)
            self.assertEqual(result['status'],'unavailable')
            self.assertEqual(result['reason'],'reclaim_busy')
            self.assertFalse((root/'runtime'/'storage'/'reservations.json').exists())

class ConfigurationTest(unittest.TestCase):
    def test_setting_floor_invalidates_readiness_before_and_after(self):
        from sandbox.commands import remote
        args = SimpleNamespace(name='remote-a',storage_floor_gb=30)
        events = []
        with mock.patch.object(remote.sr,'get_remote',return_value={'name':'remote-a'}), \
                mock.patch.object(remote.sr,'put_remote',side_effect=lambda *a,**k: events.append('store') or k), \
                mock.patch.object(remote,'_invalidate_readiness',side_effect=lambda name,**k: events.append('before' if k.get('before') else 'after')), \
                mock.patch('builtins.print'):
            remote._cmd_set_storage_floor(args, True)
        self.assertEqual(events,['before','store','after'])

    def test_remote_list_reports_floor(self):
        from sandbox.commands import remote
        args = SimpleNamespace(project_dir=None)
        with mock.patch.object(remote.sr,'list_remotes',return_value={'remote-a':{'storage_floor_gb':30}}), \
                mock.patch.object(remote.sr,'check_reachable_diagnostic',return_value={'reachable':True}), \
                mock.patch('builtins.print') as output:
            remote._cmd_list(args, True)
        self.assertEqual(json.loads(output.call_args.args[0])['remotes'][0]['storage_floor_gb'],30)


class OperationReservationTests(unittest.TestCase):
    def run_at(self, root, now, *, token=None, estimate=300):
        import contextlib,io
        request = {'target':str(root/'deploy-src'/'app'),'floor_bytes':100,
                   'default_estimate':0,'minimum_estimate':estimate,
                   'window_seconds':sf.RESERVATION_WINDOW_SECONDS,'reservations':None}
        if token is not None:
            request.update(reservation_id=token,operation_expiry=sf.OPERATION_RESERVATION_EXPIRY)
        out = io.StringIO()
        with mock.patch('time.time',return_value=now), \
             mock.patch('os.statvfs',return_value=SimpleNamespace(f_bavail=1000,f_frsize=1)), \
             mock.patch('subprocess.run',side_effect=AssertionError('unexpected child')), \
             contextlib.redirect_stdout(out):
            exec(sf._PROGRAM,{'REQUEST':request})
        return json.loads(out.getvalue())

    def test_operation_survives_expiry_then_explicit_release_frees_only_it(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);(root/'deploy-src').mkdir()
            first = self.run_at(root,10000,token='a'*32)
            self.assertTrue(first['admitted'])
            ledger = root/'runtime'/'storage'/'reservations.json'
            entries = json.loads(ledger.read_text())
            self.assertEqual(entries[0]['expires_at'],sf.OPERATION_RESERVATION_EXPIRY)
            # An ordinary 45-minute reservation expires, while this import does not.
            ordinary = {'id':'ordinary','estimate_bytes':111,'expires_at':10000+2700}
            ledger.write_text(json.dumps(entries+[ordinary]))
            later = self.run_at(root,10000+48*3600,estimate=700)
            self.assertEqual(later['reserved_bytes'],300)
            self.assertFalse(later['admitted'])
            sf.release_storage_reservation(str(ledger.parent),'a'*32)
            self.assertEqual(json.loads(ledger.read_text()),[ordinary])
            sf.release_storage_reservation(str(ledger.parent),'a'*32)  # Idempotent.
            final = self.run_at(root,10000+48*3600,estimate=700)
            self.assertTrue(final['admitted'])
            self.assertEqual(final['reserved_bytes'],0)

    def test_replay_cannot_shorten_or_duplicate_capacity_and_peer_release_is_scoped(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'deploy-src').mkdir()
            self.run_at(root,10000,token='a'*32)
            replay=self.run_at(root,20000,token='a'*32,estimate=100)
            self.assertEqual(replay['estimate_bytes'],300)
            self.run_at(root,20000,token='b'*32,estimate=200)
            ledger=root/'runtime'/'storage'/'reservations.json'
            self.assertEqual(len(json.loads(ledger.read_text())),2)
            sf.release_storage_reservation(str(ledger.parent),'a'*32)
            remaining=json.loads(ledger.read_text())
            self.assertEqual([e['id'] for e in remaining],['b'*32])
            self.assertEqual(remaining[0]['expires_at'],sf.OPERATION_RESERVATION_EXPIRY)

    def test_quarantined_worker_reservation_is_not_reused_after_controller_expiry(self):
        import contextlib,hashlib,io,os,sys,tempfile
        from pathlib import Path
        from sandbox.transports import remote_local_images as transfer
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'deploy-src').mkdir()
            self.run_at(root,10000,token='a'*32)
            data=b'archive';chunks=[data,b''];scope={}
            args=['worker',str(root/'runtime'/'host-local-images'/'uploads'),'bundle-1.tar',
                  'sha256:'+hashlib.sha256(data).hexdigest(),str(len(data)),'37','a'*32]
            with mock.patch.object(sys,'argv',args),mock.patch('os.read',side_effect=lambda *_:chunks.pop(0)), \
                 mock.patch('subprocess.run',side_effect=__import__('subprocess').TimeoutExpired('docker',37)), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_state:
                    exec(transfer._STAGE_PROGRAM,scope)
            self.assertEqual(exit_state.exception.code,34)
            os.close(scope['fence'])  # Worker gone; persistence must still protect capacity.
            marker=root/'runtime'/'host-local-images'/'load-unverified'
            self.assertEqual(json.loads(marker.read_text())['reservation_id'],'a'*32)
            decision=self.run_at(root,10000+48*3600,estimate=700)
            self.assertFalse(decision['admitted'])
            self.assertEqual(decision['reserved_bytes'],300)

    def test_successful_worker_releases_before_acknowledgement(self):
        import contextlib,hashlib,io,os,sys,tempfile
        from pathlib import Path
        from sandbox.transports import remote_local_images as transfer
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'deploy-src').mkdir()
            self.run_at(root,10000,token='a'*32)
            data=b'archive';chunks=[data,b''];scope={};out=io.StringIO()
            args=['worker',str(root/'runtime'/'host-local-images'/'uploads'),'bundle-1.tar',
                  'sha256:'+hashlib.sha256(data).hexdigest(),str(len(data)),'37','a'*32]
            with mock.patch.object(sys,'argv',args),mock.patch('os.read',side_effect=lambda *_:chunks.pop(0)), \
                 mock.patch('subprocess.run',return_value=SimpleNamespace(returncode=0)), \
                 contextlib.redirect_stdout(out):
                exec(transfer._STAGE_PROGRAM,scope)
            os.close(scope['fence'])
            self.assertEqual(json.loads(out.getvalue().splitlines()[-1])['state'],'loaded')
            self.assertEqual(json.loads((root/'runtime'/'storage'/'reservations.json').read_text()),[])
            self.assertFalse((root/'runtime'/'host-local-images'/'load-unverified').exists())



class FinalReleaseCompatibilityTests(unittest.TestCase):
    def test_old_client_rewrite_during_load_is_released_and_tightened(self):
        import contextlib,hashlib,io,os,stat,sys,tempfile
        from pathlib import Path
        from sandbox.transports import remote_local_images as transfer
        from tests.fixtures.local_image_legacy_programs import STORAGE_ADMISSION_V1
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'deploy-src').mkdir()
            OperationReservationTests().run_at(root,10000,token='a'*32)
            ledger=root/'runtime'/'storage'/'reservations.json'
            data=b'archive';chunks=[data,b''];scope={};out=io.StringIO()
            args=['worker',str(root/'runtime'/'host-local-images'/'uploads'),'bundle-1.tar',
                  'sha256:'+hashlib.sha256(data).hexdigest(),str(len(data)),'37','a'*32]
            def old_client(*args,**kwargs):
                request={'target':str(root/'deploy-src'/'app'),'floor_bytes':1,'default_estimate':100,
                         'window_seconds':2700,'reservations':None}
                previous=os.umask(0o022)
                try:
                    with contextlib.redirect_stdout(io.StringIO()):exec(STORAGE_ADMISSION_V1,{'REQUEST':request})
                finally:os.umask(previous)
                self.assertEqual(stat.S_IMODE(ledger.stat().st_mode),0o644)
                return SimpleNamespace(returncode=0)
            with mock.patch.object(sys,'argv',args),mock.patch('os.read',side_effect=lambda *_:chunks.pop(0)), \
                 mock.patch('subprocess.run',side_effect=old_client),contextlib.redirect_stdout(out):
                exec(transfer._STAGE_PROGRAM,scope)
            os.close(scope['fence'])
            self.assertEqual(json.loads(out.getvalue().splitlines()[-1])['state'],'loaded')
            retained=json.loads(ledger.read_text())
            self.assertEqual(len(retained),1);self.assertEqual(retained[0]['estimate_bytes'],100)
            self.assertNotEqual(retained[0]['id'],'a'*32)
            self.assertEqual(stat.S_IMODE(ledger.stat().st_mode),0o600)
            self.assertFalse((root/'runtime'/'host-local-images'/'load-unverified').exists())

    def test_release_waits_for_contention_but_has_a_bound(self):
        import fcntl,os,tempfile,threading
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'deploy-src').mkdir()
            OperationReservationTests().run_at(root,10000,token='a'*32)
            store=root/'runtime'/'storage';ledger=store/'reservations.json';ledger.chmod(0o644)
            with (store/'reservations.lock').open('r+b') as holder:
                fcntl.flock(holder,fcntl.LOCK_EX)
                with self.assertRaises(TimeoutError):sf.release_storage_reservation(str(store),'a'*32,lock_timeout=0.01)
                timer=threading.Timer(0.1,holder.close);timer.start()
                try:sf.release_storage_reservation(str(store),'a'*32,lock_timeout=1)
                finally:timer.join()
            self.assertEqual(json.loads(ledger.read_text()),[])
            self.assertEqual(ledger.stat().st_mode & 0o777,0o600)

    def test_reconcile_killed_upload_and_reject_delayed_worker(self):
        import fcntl,hashlib,os,selectors,subprocess,sys,tempfile
        from pathlib import Path
        from sandbox.transports import remote_local_images as transfer
        from tests.subprocess_support import run_test_process,synthetic_environment
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'deploy-src').mkdir()
            OperationReservationTests().run_at(root,10000,token='a'*32)
            ledger=root/'runtime'/'storage'/'reservations.json'
            entries=json.loads(ledger.read_text())
            peer={**entries[0],'id':'b'*32,'target':str(root/'deploy-src'/'peer')}
            ordinary={'id':'ordinary','estimate_bytes':1,'expires_at':sf.OPERATION_RESERVATION_EXPIRY,'target':str(root/'deploy-src'/'app')}
            ledger.write_text(json.dumps(entries+[peer,ordinary]))
            engine=root/'runtime'/'host-local-images';engine.mkdir(mode=0o700)
            (engine/'engine.lock').touch(mode=0o600);(engine/'load-fence.lock').touch(mode=0o600)
            args=[sys.executable,'-c',transfer._STAGE_PROGRAM,str(engine/'uploads'),'bundle-1.tar',
                  'sha256:'+hashlib.sha256(b'archive').hexdigest(),'7','37','a'*32]
            with (engine/'engine.lock').open('r+b') as lease:
                fcntl.flock(lease,fcntl.LOCK_EX)
                process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    env=synthetic_environment({'PATH':'/nonexistent'}))
                try:
                    selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
                    self.assertTrue(selector.select(2));selector.close()
                    self.assertEqual(json.loads(process.stdout.readline())['state'],'ready')
                    def reconcile():
                        q=run_test_process([sys.executable,'-c',transfer._RECONCILE_PROGRAM,str(engine),str(root/'deploy-src'/'app')],capture_output=True,text=True,timeout=2)
                        self.assertEqual(q.returncode,0,q.stderr);return json.loads(q.stdout)['safe']
                    self.assertFalse(reconcile())  # Live acknowledged worker owns fence.
                    process.kill();process.wait(timeout=2)
                    self.assertFalse((engine/'load-unverified').exists())
                    (engine/'load-unverified').write_text('uncertain')
                    self.assertFalse(reconcile());(engine/'load-unverified').unlink()
                    self.assertTrue(reconcile())
                    self.assertEqual(json.loads(ledger.read_text()),[peer,ordinary])
                    delayed=run_test_process(args,input=b'archive',capture_output=True,timeout=2,env={'PATH':'/nonexistent'})
                    self.assertNotEqual(delayed.returncode,0)
                    self.assertNotIn(b'"state":"ready"',delayed.stdout)
                    self.assertFalse((engine/'load-unverified').exists())
                    self.assertEqual(json.loads(ledger.read_text()),[peer,ordinary])
                finally:
                    if process.poll() is None:process.kill();process.wait(timeout=2)
                    process.stdin.close();process.stdout.close();process.stderr.close()

    def test_worker_early_checks_release_without_marker(self):
        import hashlib,os,sys,tempfile
        from pathlib import Path
        from sandbox.transports import remote_local_images as transfer
        from tests.subprocess_support import run_test_process
        for failure in ('name','unsafe-root','unsafe-fence','marker'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);(root/'deploy-src').mkdir()
                OperationReservationTests().run_at(root,10000,token='a'*32)
                engine=root/'runtime'/'host-local-images';engine.mkdir(mode=0o700)
                uploads=engine/'uploads';uploads.mkdir(mode=0o777 if failure=='unsafe-root' else 0o700)
                if failure=='unsafe-root':uploads.chmod(0o777)
                if failure=='unsafe-fence':(engine/'load-fence.lock').touch(mode=0o644)
                if failure=='marker':(engine/'load-unverified').write_text('other operation')
                argv=[sys.executable,'-c',transfer._STAGE_PROGRAM,str(uploads),'../bad' if failure=='name' else 'bundle-1.tar',
                      'sha256:'+hashlib.sha256(b'archive').hexdigest(),'7','37','a'*32]
                q=run_test_process(argv,input=b'archive',capture_output=True,timeout=2,env={'PATH':'/nonexistent'})
                self.assertNotEqual(q.returncode,0)
                self.assertNotIn(b'"state":"ready"',q.stdout)
                self.assertEqual(json.loads((root/'runtime'/'storage'/'reservations.json').read_text()),[])



class FinalRecoveryDocumentationTests(unittest.TestCase):
    def test_runbook_recovers_0644_ledger_without_importable_sandbox(self):
        import sys,tempfile
        from pathlib import Path
        from tests.subprocess_support import run_test_process
        repo=Path(__file__).resolve().parent.parent
        section=(repo/'docs/remote-hosting.md').read_text().split('### V2 reservation lifecycle and quarantine recovery')[1]
        command=section.split('```sh\n')[1].split('```')[0]
        self.assertIn(sf.RESERVATION_RELEASE_PROGRAM,command)
        self.assertNotIn('from sandbox',command)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'deploy-src').mkdir()
            OperationReservationTests().run_at(root,10000,token='a'*32)
            ledger=root/'runtime'/'storage'/'reservations.json';ledger.chmod(0o644)
            engine=root/'runtime'/'host-local-images';engine.mkdir(mode=0o700)
            (engine/'engine.lock').touch(mode=0o600)
            (engine/'load-fence.lock').touch(mode=0o400)
            (engine/'load-unverified').write_text(json.dumps({'reservation_id':'a'*32}))
            result=run_test_process(['sh','-c',command],cwd=root,capture_output=True,text=True,timeout=2,
                env={'SANDBOX_HOME':str(root),'PYTHONPATH':'/nonexistent','PATH':'/usr/bin:/bin'})
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((engine/'load-unverified').exists())
            self.assertEqual((engine/'load-fence.lock').stat().st_mode & 0o777,0o600)
            self.assertEqual(ledger.stat().st_mode & 0o777,0o600)
            self.assertEqual(json.loads(ledger.read_text()),[])



class UnsafeReservationLedgerTests(unittest.TestCase):
    def test_symlink_writable_foreign_and_hardlinked_ledgers_refuse(self):
        import os,tempfile
        from pathlib import Path
        for case in ('symlink','group-write','world-write','both-write','foreign-owner','hardlink'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);(root/'deploy-src').mkdir()
                OperationReservationTests().run_at(root,10000,token='a'*32)
                store=root/'runtime'/'storage';ledger=store/'reservations.json'
                before=ledger.read_bytes();target=ledger
                if case in ('symlink','hardlink'):
                    target=store/'original.json';ledger.rename(target)
                    if case=='symlink':ledger.symlink_to(target.name)
                    else:os.link(target,ledger)
                mode={'group-write':0o620,'world-write':0o602,'both-write':0o666,'foreign-owner':0o644}.get(case,0o600)
                target.chmod(mode)
                info=target.stat();real_fstat=os.fstat
                def fstat(fd):
                    value=real_fstat(fd)
                    if case=='foreign-owner' and value.st_ino==info.st_ino and value.st_dev==info.st_dev:
                        fields=list(value);fields[4]=value.st_uid+1;return os.stat_result(fields)
                    return value
                with mock.patch('os.fstat',side_effect=fstat):
                    with self.assertRaises((ValueError,OSError)):
                        sf.release_storage_reservation(str(store),'a'*32)
                self.assertEqual(target.read_bytes(),before)
                self.assertEqual(target.stat().st_mode & 0o777,mode)
                if case=='symlink':self.assertTrue(ledger.is_symlink())


if __name__ == "__main__":
    unittest.main()
