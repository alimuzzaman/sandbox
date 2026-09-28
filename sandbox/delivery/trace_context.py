"""Explicit local owner composition for deployment trace diagnostics."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

from sandbox.config.facade import project_identity
from sandbox.core._paths import RUNTIME_DIR
from .trace_models import fail


class TraceProducerRegistry:
    """Fixed producers and controller identity; no caller-selected imports."""

    def scope(self, project_dir, *, write=False):
        if (not isinstance(project_dir, str) or not project_dir.strip() or '\x00' in project_dir
                or len(project_dir.encode('utf-8')) > 4096):
            fail()
        root = Path(project_dir).expanduser().resolve()
        result = {
            'project_identity': project_identity({'root': root})['identity'],
            'project_root_digest': 'sha256:' + hashlib.sha256(str(root).encode()).hexdigest(),
        }
        if write:
            from .context import source_commit
            from sandbox.core._remote import _remote_mcp_runtime_revision
            try:
                commit = source_commit(Path(__file__).resolve().parents[2])
            except (OSError, ValueError, subprocess.SubprocessError):
                commit = None
            result['controller_context'] = {
                'source_commit': commit,
                'source_runtime_revision': _remote_mcp_runtime_revision(),
                'installed_controller_runtime_revision': None,
            }
        return result

    def get_producer(self, producer_id):
        from .producers.manifest import get_producer
        return get_producer(producer_id)

    def capabilities(self):
        from .manifest import available_delivery_capabilities
        from .producers.manifest import descriptors
        from sandbox.core._remote import _remote_mcp_runtime_revision
        available = available_delivery_capabilities()
        supported = all(available.get(name) == 1 for name in
                        ('deployment_trace_v1', 'lenzora_hosted_trace_v1'))
        return {
            'schema_version': 1, 'ok': supported,
            'capability': 'deployment_trace_v1', 'capability_version': 1,
            'runtime_revision': _remote_mcp_runtime_revision(),
            'producers': descriptors() if supported else [],
            'limits': {'input_bytes': 32768, 'document_bytes': 131072,
                       'response_bytes': 262144, 'query_seconds': 5,
                       'sqlite_busy_seconds': 2, 'database_bytes': 134217728,
                       'rollback_bytes': 134217728, 'protected': 128,
                       'terminal_global': 512, 'terminal_per_target': 64,
                       'terminal_days': 30, 'guards': 4096,
                       'events': 64, 'event_bytes': 1024, 'links': 32,
                       'mutation_receipts': 256, 'recovery_links': 10},
        }


def build_trace_service():
    from .trace_service import TraceService
    from .trace_repository import TraceRepository
    from .repository import DeliveryRepository
    from .models import request_scope
    from sandbox.jobs.registry import read_trace_job_evidence
    from sandbox.hosting.recovery.repository import RecoveryRepository
    from sandbox.hosting.images.activation.status import read_trace_status

    outcomes = DeliveryRepository(RUNTIME_DIR.parent)
    activations = RecoveryRepository()

    def job_reader(candidate, scope, budget):
        return read_trace_job_evidence(
            RUNTIME_DIR / 'jobs' / 'registry.sqlite3', candidate['job_id'],
            role=candidate['role'], request_id=candidate['request_id'],
            control_root_digest=candidate['control_root_digest'],
            control_source_commit=candidate['control_source_commit'],
            submission_digest=candidate['submission_digest'], budget=budget)

    def activation_reader(request_id, scope, budget):
        requested = scope.get('requested_target') or {}

        class BoundActivationReader:
            def read_trace_activation_nested(self, _key, *, budget):
                return activations.read_trace_activation_nested(
                    None, budget=budget, request_id=request_id,
                    remote=requested.get('remote_name'), environment=requested.get('environment'))

        return read_trace_status(BoundActivationReader(), '', request_id, budget=budget)

    def recovery_reader(original, scope, budget):
        target = scope['delivery_target']
        result = outcomes.read_related_recoveries(original, request_scope(target), budget=budget)
        fields = ('project_identity', 'project_root_digest', 'target_kind',
                  'remote_name', 'environment', 'label')
        retained = []
        for operation in result['recoveries']:
            budget.check()
            if all(operation['target'][key] == target[key] for key in fields):
                retained.append(operation)
            else:
                result.update(state='partial', reason='binding_mismatch')
                result['omitted'] += 1
        result['recoveries'] = retained
        return result

    return TraceService(TraceRepository(RUNTIME_DIR.parent), TraceProducerRegistry(),
                        job_reader, activation_reader, outcomes.read_trace_operation,
                        recovery_reader, None)
