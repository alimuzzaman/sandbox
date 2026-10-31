"""Spec 066: reclaiming a workspace removes its whole compose project."""
import json
import unittest

from sandbox.resources import remote
from sandbox.resources.workspace_teardown import teardown_workspace_stack

WS = "proj-workspace-0123456789abcd"


class FakeDocker:
    def __init__(self, labels, members=None, volumes=None, volume_users=None,
                 networks=None, fail=(), engine_projects=(), volume_projects=()):
        self.engine_projects = list(engine_projects)
        self.volume_projects = list(volume_projects)
        self.labels = labels
        self.members = members or {}
        self.volumes = volumes or {}
        self.volume_users = volume_users or {}
        self.networks = networks or {}
        self.fail = set(fail)
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        joined = " ".join(argv)
        if argv[:2] == ["docker", "inspect"]:
            if "inspect" in self.fail:
                return 1, "", "err"
            return 0, "\n".join(json.dumps(self.labels[c]) for c in argv[4:]), ""
        if argv[:4] == ["docker", "ps", "-a", "--format"]:
            return 0, "\n".join(self.engine_projects), ""
        if argv[:4] == ["docker", "volume", "ls", "--format"]:
            return 0, "\n".join(self.volume_projects), ""
        if argv[:3] == ["docker", "ps", "-aq"]:
            flt = argv[-1]
            if flt.startswith("label="):
                return 0, "\n".join(self.members.get(flt.split("=", 2)[2], ())), ""
            return 0, "\n".join(self.volume_users.get(flt.split("=", 1)[1], ())), ""
        if argv[:3] == ["docker", "volume", "ls"]:
            return 0, "\n".join(self.volumes.get(argv[-1].split("=", 2)[2], ())), ""
        if argv[:3] == ["docker", "network", "ls"]:
            return 0, "\n".join(self.networks.get(argv[-1].split("=", 2)[2], ())), ""
        if argv[:3] == ["docker", "rm", "-f"]:
            return (1, "", "boom") if "rm" in self.fail else (0, "", "")
        if argv[:3] == ["docker", "volume", "rm"]:
            return (1, "", "boom") if "vrm" in self.fail else (0, "", "")
        if argv[:3] == ["docker", "network", "rm"]:
            if argv[3] == "shared-net":
                return 1, "", "error: network has active endpoints"
            return 0, "", ""
        return 0, "", ""


PROJECT = "sandbox-" + WS


class TeardownTest(unittest.TestCase):
    def test_removes_project_containers_volumes_networks(self):
        docker = FakeDocker(
            {"c1": {"com.docker.compose.project": PROJECT}},
            members={PROJECT: ["c1", "pg"]},
            volumes={PROJECT: ["v-node", "v-shared"]},
            volume_users={"v-shared": ["other"]},
            networks={PROJECT: ["n1", "shared-net"]})
        result = teardown_workspace_stack(docker, WS, ["c1"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["projects"], [PROJECT])
        self.assertEqual(result["containers_removed"], 2)
        self.assertEqual(result["volumes_removed"], ["v-node"])
        self.assertEqual(result["volumes_kept_shared"], ["v-shared"])
        self.assertEqual(result["networks_removed"], ["n1"])
        self.assertEqual(result["networks_kept"], ["shared-net"])

    def test_hosted_and_foreign_projects_never_touched(self):
        docker = FakeDocker({
            "c1": {"com.docker.compose.project": "sandbox-host-lenzora-production"},
            "c2": {"com.docker.compose.project": "sandbox-other-workspace-ffffffffffffff"},
            "c3": {"com.docker.compose.project": "lenzora"},
            "c4": {}})
        result = teardown_workspace_stack(docker, WS, ["c1", "c2", "c3", "c4"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["projects"], [])
        self.assertFalse(any(call[:3] == ["docker", "rm", "-f"] for call in docker.calls))

    def test_stack_found_without_bound_containers(self):
        docker = FakeDocker({}, members={PROJECT: ["pg"]}, volumes={PROJECT: ["db"]},
                            engine_projects=[PROJECT, "sandbox-host-lenzora-production",
                                             "sandbox-proj-workspace-ffffffffffffff"],
                            volume_projects=[PROJECT + "-qa"])
        result = teardown_workspace_stack(docker, WS, [])
        self.assertEqual(result["projects"], [PROJECT, PROJECT + "-qa"])
        self.assertEqual(result["containers_removed"], 1)
        self.assertEqual(result["volumes_removed"], ["db"])

    def test_base_deployment_owns_no_stack(self):
        docker = FakeDocker({}, engine_projects=["sandbox-proj",
                                                 "sandbox-proj-workspace-0123456789abcd"])
        result = teardown_workspace_stack(docker, "proj", [])
        self.assertEqual(result["projects"], [])
        self.assertEqual(docker.calls, [])

    def test_engine_listing_failure_keeps_source(self):
        class Broken(FakeDocker):
            def __call__(self, argv, timeout):
                if argv[:4] == ["docker", "ps", "-a", "--format"]:
                    return 1, "", "down"
                return super().__call__(argv, timeout)
        self.assertFalse(teardown_workspace_stack(Broken({}), WS, [])["ok"])

    def test_short_or_truncated_names(self):
        truncated = "sandbox-" + WS[:26]
        docker = FakeDocker({"c1": {"com.docker.compose.project": truncated}},
                            members={truncated: ["c1"]})
        self.assertEqual(teardown_workspace_stack(docker, WS, ["c1"])["projects"], [truncated])
        tiny = FakeDocker({"c1": {"com.docker.compose.project": "sandbox-proj"}})
        self.assertEqual(teardown_workspace_stack(tiny, WS, ["c1"])["projects"], [])

    def test_failures_are_reported(self):
        for fail in ("inspect", "rm", "vrm"):
            docker = FakeDocker({"c1": {"com.docker.compose.project": PROJECT}},
                                members={PROJECT: ["c1"]}, volumes={PROJECT: ["v"]},
                                fail=(fail,))
            self.assertFalse(teardown_workspace_stack(docker, WS, ["c1"])["ok"], fail)

    def test_no_stack_is_a_noop(self):
        docker = FakeDocker({})
        result = teardown_workspace_stack(docker, WS, [])
        self.assertTrue(result["ok"])
        self.assertEqual(result["projects"], [])


class ProgramTest(unittest.TestCase):
    def test_program_embeds_helpers_and_compiles(self):
        source = remote._program({"action": "inventory"})
        self.assertNotIn("__WORKSPACE_TEARDOWN__", source)
        self.assertIn("def teardown_workspace_stack", source)
        self.assertIn("def job_workspace_references", source)
        compile(source, "<remote>", "exec")

    def test_source_kept_when_teardown_fails(self):
        source = remote._REMOTE_PROGRAM
        start = source.index("teardown = teardown_workspace_stack(")
        window = source[start:start + 2300]
        self.assertIn('if teardown is not None and not teardown.get("ok"):\n'
                      "                absent, elevated, failure = False, False, None", window)
        self.assertIn("stack_teardown_incomplete", window)


if __name__ == "__main__":
    unittest.main()


class RecheckTest(unittest.TestCase):
    def test_finished_idle_candidates_recheck_registry_before_teardown(self):
        source = remote._REMOTE_PROGRAM
        start = source.index('if kind == "worktree":', source.index('def reclaim_run'))
        window = source[start:start + 3600]
        self.assertIn("read_resource_index", window)
        self.assertIn("busy = True", window)
        self.assertIn("job_active_since_plan", window)
        self.assertLess(start, source.index("teardown = teardown_workspace_stack("))

class SafetyRegressionTest(unittest.TestCase):
    def test_bound_hosted_container_blocks_source_removal(self):
        docker = FakeDocker({'host':{'com.docker.compose.project':'sandbox-host-lenzora-production'}})
        result = teardown_workspace_stack(docker, WS, ['host'])
        self.assertFalse(result['ok'])
        self.assertFalse(any(c[:3] == ['docker','rm','-f'] for c in docker.calls))

    def test_every_delete_is_recorded_first(self):
        recorded = []
        docker = FakeDocker({}, members={PROJECT:['pg']}, volumes={PROJECT:['db']},
                            networks={PROJECT:['net']}, engine_projects=[PROJECT])
        def record(kind, locator):
            recorded.append((kind,locator,len(docker.calls)))
        result = teardown_workspace_stack(docker, WS, [], record=record)
        self.assertTrue(result['ok'])
        self.assertEqual([(k,n) for k,n,_ in recorded], [('container','pg'),('volume','db'),('network','net')])
        for kind, name, index in recorded:
            self.assertTrue(any(name in call for call in docker.calls[index:]))

    def test_manifest_failure_prevents_engine_mutation(self):
        docker = FakeDocker({}, members={PROJECT:['pg']}, engine_projects=[PROJECT])
        def record(kind, locator):
            raise OSError('disk full')
        with self.assertRaises(OSError):
            teardown_workspace_stack(docker, WS, [], record=record)
        self.assertFalse(any(c[1] in ('stop','rm') for c in docker.calls))

class ReclaimExecutionTest(unittest.TestCase):
    def execute(self, *, source_exists=True, jobs=None, reservations=None, reason='finished_job_idle', teardown_ok=True):
        import tempfile, time
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            deploy = home / 'deploy-src'
            deploy.mkdir()
            path = deploy / WS
            if source_exists:
                path.mkdir()
                (path / 'source').write_text('keep')
            runtime = home / 'runtime'
            if reservations is not None:
                store = runtime / 'storage'
                store.mkdir(parents=True)
                (store / 'reservations.json').write_text(json.dumps(reservations))
            request = {'action':'reclaim','run_id':'a'*32,'budget_seconds':30,
                       'candidates':[{'seq':1,'kind':'worktree','locator':str(path),'reason':reason}]}
            source = remote._program(request).split('\nACTIONS = {',1)[0]
            scope = {}
            exec(source,scope)
            scope.update(HOME=home, DEPLOY=deploy, RUNTIME=runtime,
                         DELETION_DIR=runtime/'deletions', hosted_site_names=lambda: set(),
                         reconcile_after_removal=lambda *a: {}, run=lambda *a: (0,'',''))
            teardown = mock.Mock(return_value={'ok':teardown_ok})
            scope['teardown_workspace_stack'] = teardown
            old = {'project_root':str(path),'lifecycle':'succeeded','cleanup_state':'retained',
                   'finished_at':time.time()-8*3600}
            with mock.patch('sandbox.jobs.registry.read_resource_index', return_value={'jobs':jobs if jobs is not None else [old]}):
                result = scope['reclaim_run']()
            return result, path.exists(), teardown.call_count

    def test_teardown_failure_preserves_source(self):
        result, exists, calls = self.execute(teardown_ok=False)
        self.assertTrue(exists)
        self.assertEqual(calls,1)
        self.assertEqual(result['outcomes'][0]['reason'],'stack_teardown_incomplete')

    def test_missing_source_still_tears_down_stack(self):
        result, exists, calls = self.execute(source_exists=False)
        self.assertFalse(exists)
        self.assertEqual(calls,1)
        self.assertEqual(result['outcomes'][0]['status'],'already_absent')

    def test_running_job_blocks_every_worktree_reason(self):
        # A malformed/foreign root is irrelevant. Use a missing idle proof to
        # ensure old plans cannot bypass the fresh recheck.
        result, exists, calls = self.execute(jobs=[], reason='finished_job_idle')
        self.assertTrue(exists)
        self.assertEqual(calls,0)
        self.assertEqual(result['outcomes'][0]['reason'],'job_active_since_plan')

    def test_unknown_reservation_target_keeps_source(self):
        import time
        result, exists, calls = self.execute(reservations=[{'expires_at':time.time()+3600,'estimate_bytes':1}])
        self.assertTrue(exists)
        self.assertEqual(calls,0)

class TerminalCICleanupTest(unittest.TestCase):
    def test_stack_failure_aborts_cleanup(self):
        from sandbox.resources import ci_teardown
        from types import SimpleNamespace
        from unittest import mock
        from pathlib import Path
        with mock.patch.object(ci_teardown, 'read_resource_index', return_value={'jobs':[]}), \
                mock.patch.object(ci_teardown, 'teardown_workspace_stack', return_value={'ok':False}) as teardown, \
                mock.patch.object(ci_teardown, '_record'):
            with self.assertRaisesRegex(RuntimeError, 'stack_teardown_incomplete'):
                ci_teardown.complete_terminal_stack(Path('/h/deploy-src')/WS, Path('/h/deploy-src'),
                                                    SimpleNamespace(path=Path('/h/jobs.sqlite3')))
        teardown.assert_called_once()

    def test_running_sibling_refuses_before_engine_calls(self):
        from sandbox.resources import ci_teardown
        from types import SimpleNamespace
        from unittest import mock
        from pathlib import Path
        checkout = Path('/h/deploy-src')/WS
        with mock.patch.object(ci_teardown, 'read_resource_index', return_value={
                'jobs':[{'project_root':str(checkout),'lifecycle':'running'}]}), \
                mock.patch.object(ci_teardown, 'teardown_workspace_stack') as teardown:
            with self.assertRaisesRegex(RuntimeError, 'active_job_protected'):
                ci_teardown.complete_terminal_stack(checkout, checkout.parent, SimpleNamespace(path=Path('/h/jobs.sqlite3')))
        teardown.assert_not_called()

    def test_hosted_name_never_reaches_engine(self):
        from sandbox.resources import ci_teardown
        from unittest import mock
        from pathlib import Path
        with mock.patch.object(ci_teardown,'teardown_workspace_stack') as teardown:
            with self.assertRaisesRegex(RuntimeError,'hosted_site'):
                ci_teardown.complete_terminal_stack(Path('/h/deploy-src/sandbox-host-p-workspace-0123456789abcd'),
                                                    Path('/h/deploy-src'), None)
        teardown.assert_not_called()
