"""Synthetic engine evidence regression tests. No Docker daemon is contacted."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from sandbox.resources.adapters import LocalResourceAdapter
from sandbox.resources.models import CleanupCandidate
from sandbox.resources.service import ResourceService
from sandbox.resources.plans import PlanStore
from tests.resource_fixtures import NOW, deep_attribution, observation
from tests.test_resource_adapters import FakeRunner
from tests.test_resource_service import FakeAdapter
from tests import test_resource_remote as remote_tests


class StorageEvidenceTests(unittest.TestCase):
    def test_volume_deep_join_uses_incarnation_identity(self):
        class Collector:
            def __init__(self, *a, **kw):
                pass
            def collect(self, **kw):
                return replace(deep_attribution(), managed_root_measurements=tuple({
                    'owner_id': row['owner_id'], 'kind': row['kind'],
                    'size_state': 'measured', 'size_bytes': 1234,
                    'source': 'scan', 'stale': False, 'status': 'complete',
                } for row in kw['managed_roots'] if row['kind'] == 'volume'))
        with tempfile.TemporaryDirectory() as temp:
            adapter = LocalResourceAdapter(Path(temp), runner=FakeRunner(),
                registry_records=lambda: {}, job_resource_records=lambda: {'jobs': [], 'artifacts': []},
                host_root=Path(temp), deep_collector_factory=Collector, clock=lambda: NOW)
            inventory = {'containers': [], 'images': [], 'networks': [], 'build_cache': [],
                'volumes': [{'Name': 'volume-one', 'CreatedAt': '2026-01-01T00:00:00Z',
                             'Mountpoint': '/engine/volumes/one'}]}
            with patch.object(adapter, '_docker_inventory', return_value=(inventory, ())):
                snapshot = adapter.observe(thorough=True, deep=True, budget_seconds=30)
            volume = next(row for row in snapshot.resources if row.kind == 'volume')
            self.assertEqual(volume.size_bytes, 1234)
            self.assertEqual(volume.size_state, 'measured')
            self.assertEqual(volume.reclaimable_bytes, 0)

    def test_image_review_preserves_all_aliases_and_stopped_refs_without_env(self):
        ns = remote_tests.TestRemoteProbeResilience()._probe_namespace()
        containers = [{'Id': 'stopped-id', 'Image': 'sha256:old',
            'State': {'Running': False, 'Status': 'exited'}, 'Config': {'Env': ['PASSWORD=hidden']}}]
        image = {'Id': 'sha256:old', 'RepoTags': ['dev:old', 'prod:keep'],
                 'RepoDigests': ['repo@sha256:old'], 'Created': '2026-01-01',
                 'Config': {'Labels': {'stage': 'prod', 'api_token': 'hidden'}}}
        metadata = ns['review_metadata']('image', image, containers)
        self.assertEqual(metadata['aliases'], ['dev:old', 'prod:keep'])
        self.assertEqual(metadata['container_ids'], ['stopped-id'])
        self.assertEqual(metadata['labels']['stage'], 'prod')
        from sandbox.resources.remote import _observation
        raw = ns['observation']('image', 'sha256:old', 'dev:old', 'unmanaged', None,
            'active', 'measured', 100, 0, engine=metadata)
        public = _observation(raw).to_dict()
        self.assertEqual(public['engine']['engine_id'], 'sha256:old')
        self.assertNotIn('hidden', json.dumps(public))
        self.assertNotIn('Env', json.dumps(public))
        self.assertEqual(public['engine']['labels']['api_token'], '[redacted]')

    def test_partial_worker_exits_nonzero(self):
        from sandbox.resources import detached
        args = SimpleNamespace(remote=None, budget=1, thorough=False, deep=False, fast=False, refresh=False)
        with patch('sandbox.resources.context.resource_service', return_value=SimpleNamespace(
                status=lambda **kw: {'ok': True, 'status': 'partial'})), redirect_stdout(io.StringIO()):
            self.assertEqual(detached.run_worker(args), 1)

    def test_partial_cleanup_does_not_claim_ok(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeAdapter((observation('a'),))
            service = ResourceService(adapter, PlanStore(Path(temp), clock=lambda: NOW), clock=lambda: NOW)
            plan = service.plan('cache')
            adapter.revalidate_map['a'] = replace(adapter.resources[0], evidence=('changed',))
            receipt = service.cleanup(plan['data']['plan_id'], confirm=True)
            self.assertEqual(receipt['status'], 'partial')
            self.assertFalse(receipt['ok'])

    def test_download_cache_is_not_eligible_without_idle_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            (home / 'runtime' / 'dl-cache').mkdir(parents=True)
            adapter = LocalResourceAdapter(home, runner=FakeRunner(), clock=lambda: NOW)
            rows, _ = adapter._path_resources(thorough=False, deadline=__import__('time').monotonic()+30,
                active_sources=set(), protected_paths={})
            cache = next(row for row in rows if row.kind == 'download_cache')
            self.assertEqual(cache.classification, 'unverified')
            self.assertEqual(cache.reclaimable_bytes, 0)
            self.assertIn('download_cache_idle_unverified', cache.references)

    def test_old_cache_plan_cannot_remove_container(self):
        from sandbox.resources.models import CleanupPlan
        with tempfile.TemporaryDirectory() as temp:
            item = observation('stopped', kind='container')
            adapter = FakeAdapter((item,))
            store = PlanStore(Path(temp), clock=lambda: NOW)
            store.save(CleanupPlan.create(adapter.target(), 'cache',
                [CleanupCandidate.from_observation(item)], [], now=NOW))
            plan_id = next(Path(temp).glob('*.json')).stem
            receipt = ResourceService(adapter, store, clock=lambda: NOW).cleanup(plan_id, confirm=True)
            self.assertFalse(receipt['ok'])
            self.assertEqual(adapter.removed, [])

    def test_filesystem_binding_survives_public_roundtrip(self):
        from sandbox.resources.attribution import FilesystemObservation
        original = replace(deep_attribution().filesystems[0], container_data_root='/engine/docker',
                           device='8:1', mount_point='/')
        public = original.to_dict()
        self.assertEqual(public['container_data_root'], '/engine/docker')
        self.assertEqual(FilesystemObservation.from_dict(public), original)

    def test_apply_subset_and_capacity_target_are_exact_and_rechecked(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeAdapter((observation('a', kind='build_cache'), observation('b', kind='build_cache')),
                                  capacity_scope_id='root-capacity')
            original_observe = adapter.observe
            def observe(*, thorough, budget_seconds, progress=None, focus=None, deep=False):
                snap = original_observe(thorough=thorough, budget_seconds=budget_seconds,
                                        progress=progress, focus=focus, deep=deep)
                available = 50 if adapter.removed else 10
                return replace(snap, capacity={**snap.capacity, 'available_bytes': available,
                    'used_bytes': snap.capacity['total_bytes'] - snap.capacity['reserved_bytes'] - available})
            adapter.observe = observe
            service = ResourceService(adapter, PlanStore(Path(temp), clock=lambda: NOW), clock=lambda: NOW)
            plan = service.plan('cache')
            receipt = service.cleanup(plan['data']['plan_id'], confirm=True,
                resource_ids=('a', 'b'), stop_at_available_bytes=40, capacity_scope_id='root-capacity')
            self.assertTrue(receipt['ok'])
            self.assertEqual(adapter.removed, ['a'])
            self.assertEqual(receipt['data']['outcomes'][1]['reason'], 'capacity_target_reached')

    def test_apply_rejects_unknown_subset_without_consuming_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeAdapter((observation('a', kind='build_cache'), observation('b', kind='build_cache')))
            store = PlanStore(Path(temp), clock=lambda: NOW)
            service = ResourceService(adapter, store, clock=lambda: NOW)
            plan = service.plan('cache')
            plan_id = plan['data']['plan_id']
            rejected = service.cleanup(plan_id, confirm=True, resource_ids=('missing',))
            self.assertFalse(rejected['ok'])
            self.assertEqual(store.load(plan_id).state, 'planned')
            receipt = service.cleanup(plan_id, confirm=True, resource_ids=('b',))
            self.assertTrue(receipt['ok'])
            self.assertEqual(adapter.removed, ['b'])

    def test_capacity_scope_mismatch_refuses_before_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeAdapter((observation('a', kind='build_cache'),), capacity_scope_id='root')
            service = ResourceService(adapter, PlanStore(Path(temp), clock=lambda: NOW), clock=lambda: NOW)
            plan = service.plan('cache')
            receipt = service.cleanup(plan['data']['plan_id'], confirm=True,
                stop_at_available_bytes=40, capacity_scope_id='other-mount')
            self.assertFalse(receipt['ok'])
            self.assertEqual(adapter.removed, [])

    def test_engine_metadata_changes_invalidate_candidate(self):
        item = replace(observation('image', kind='image', locator='image'), engine={
            'engine_id': 'image', 'created_at': '2026-01-01', 'aliases': ['dev:old'],
            'labels': {'stage': 'dev'}, 'references_complete': True,
            'labels_redacted': False, 'container_ids': []})
        changed = replace(item, engine={**item.engine, 'aliases': ['prod:keep']})
        self.assertFalse(ResourceService._still_matches(CleanupCandidate.from_observation(item), changed))

    def test_normal_local_retry_does_not_reuse_partial_directory_cache(self):
        from sandbox.resources.attribution import DeepAttributionCollector
        with tempfile.TemporaryDirectory() as temp:
            collector = DeepAttributionCollector(FakeRunner(), host_root=Path(temp), sandbox_home=Path(temp))
            with patch.object(collector, '_directory_cache_read', return_value={
                'mounts': {'/': {'created_at': 10, 'complete': False, 'rows': [[1, '/var']]}}}):
                self.assertIsNone(collector._directory_cache_lookup('/', mode='auto', now=11))

    def test_capacity_cli_parses_exact_subset(self):
        import argparse
        from sandbox.commands.resources import configure_parser
        parser = argparse.ArgumentParser()
        configure_parser(parser)
        args = parser.parse_args(['cleanup', '--plan-id', 'a'*32, '--resource-id', 'image-a',
            '--resource-id', 'image-b', '--stop-at-available-bytes', '42949672960',
            '--capacity-scope-id', 'filesystem-root', '--confirm'])
        self.assertEqual(args.resource_id, ['image-a', 'image-b'])
        self.assertEqual(args.stop_at_available_bytes, 42949672960)

    def test_local_remote_review_projection_parity(self):
        from sandbox.resources.engine_review import review_metadata
        ns = remote_tests.TestRemoteProbeResilience()._probe_namespace()
        for kind, row in (
            ('image', {'Id': 'sha256:one', 'RepoTags': ['dev:old', 'prod:keep'],
                       'Config': {'Labels': {'stage': 'prod', 'password': 'hidden'}}}),
            ('container', {'Id': 'c', 'Image': 'sha256:one', 'RestartCount': 2,
                           'State': {'Status': 'exited', 'Running': False, 'Error': 'hidden',
                                     'Health': {'Status': 'unhealthy', 'Log': ['hidden']}}}),
            ('volume', {'Name': 'v', 'CreatedAt': '2026-01-01', 'Driver': 'local',
                        'Options': {'auth': 'https://name:hidden@example.test'}})):
            with self.subTest(kind=kind):
                left = review_metadata(kind, row, [], True)
                right = ns['review_metadata'](kind, row, [], True)
                self.assertEqual(left, right)
                self.assertNotIn('hidden', json.dumps(left))

    def test_docker_storage_uses_engine_data_root(self):
        ns = remote_tests.TestRemoteProbeResilience()._probe_namespace()
        ns['run'] = lambda argv, timeout: (0, json.dumps('/srv/engine') if argv[:2] == ['docker', 'info'] else '1 path', '')
        ns['index_paths'] = lambda *a, **kw: None
        ns['indexed_size'] = lambda path: 1024
        ns['cached_directory_size'] = lambda path: None
        rows, _ = ns['docker_storage_resources'](True)
        docker_rows = [row for row in rows if row['locator'] != '/var/lib/containerd']
        self.assertTrue(docker_rows)
        self.assertTrue(all(row['locator'].startswith('/srv/engine/') for row in docker_rows))

    def test_image_plan_requires_complete_unredacted_review(self):
        for engine in (None, {'engine_id': 'image', 'aliases': [], 'labels': {},
                             'references_complete': False, 'created_at': '2026-01-01', 'labels_redacted': False}):
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as temp:
                image = replace(observation('image', kind='image', locator='image'), engine=engine)
                service = ResourceService(FakeAdapter((image,)), PlanStore(Path(temp), clock=lambda: NOW), clock=lambda: NOW)
                payload = service.plan('cache', resource_ids=('image',))
                self.assertFalse(payload['ok'])

    def test_pre_remove_read_failure_is_not_a_deletion_failure(self):
        from sandbox.resources.service import RevalidationUnavailable
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeAdapter((observation('cache'),))
            def unavailable(candidate):
                raise RevalidationUnavailable('read failed', 'measurement_unavailable')
            adapter.remove = unavailable
            service = ResourceService(adapter, PlanStore(Path(temp), clock=lambda: NOW), clock=lambda: NOW)
            plan = service.plan('cache')
            receipt = service.cleanup(plan['data']['plan_id'], confirm=True)
            self.assertEqual(receipt['data']['outcomes'][0]['status'], 'skipped')

    def test_after_capacity_failure_preserves_known_removed_outcome(self):
        from sandbox.resources.service import ResourceError
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeAdapter((observation('a'),))
            original_observe = adapter.observe
            def observe(**kw):
                if adapter.removed:
                    raise ResourceError('read failed', 'measurement_unavailable')
                return original_observe(**{key: value for key, value in kw.items() if key != 'cancelled'})
            adapter.observe = observe
            service = ResourceService(adapter, PlanStore(Path(temp), clock=lambda: NOW), clock=lambda: NOW)
            plan = service.plan('cache')
            receipt = service.cleanup(plan['data']['plan_id'], confirm=True)
            self.assertEqual(receipt['status'], 'indeterminate')
            self.assertEqual(receipt['data']['outcomes'][0]['status'], 'removed')
            self.assertIsNone(receipt['data']['observed_reclaimed_bytes'])

    def test_capacity_recheck_rejects_target_identity_change(self):
        from tests.resource_fixtures import target
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeAdapter((observation('a', kind='build_cache'), observation('b', kind='build_cache')),
                                  capacity_scope_id='root-capacity')
            original_observe = adapter.observe
            def observe(*, thorough, budget_seconds, progress=None, focus=None, deep=False):
                snap = original_observe(thorough=thorough, budget_seconds=budget_seconds,
                                        progress=progress, focus=focus, deep=deep)
                return replace(snap, target=target(identity='changed') if adapter.removed else snap.target)
            adapter.observe = observe
            service = ResourceService(adapter, PlanStore(Path(temp), clock=lambda: NOW), clock=lambda: NOW)
            plan = service.plan('cache')
            receipt = service.cleanup(plan['data']['plan_id'], confirm=True, resource_ids=('a', 'b'),
                stop_at_available_bytes=40000, capacity_scope_id='root-capacity')
            self.assertEqual(adapter.removed, ['a'])
            self.assertEqual(receipt['status'], 'indeterminate')
