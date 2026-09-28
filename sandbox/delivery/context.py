"""Composition of delivery diagnostics with explicit read-only owner ports."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

from sandbox.config.facade import project_identity
from sandbox.core._paths import RUNTIME_DIR
from sandbox.core import _remote
from .models import canonical_digest, validate_target, now, evidence
from .repository import DeliveryRepository
from .service import (DeliveryService, normalize_recovery_projection, normalize_activation_projection,
                      normalize_job_projection, normalize_creation_receipt)


def require_delivery_capabilities(required=('delivery_outcomes_v1', 'ordinary_recovery_admission_v1')):
    """Resolve installed owner contracts before accepting covered work."""
    from .manifest import available_delivery_capabilities
    from .models import DeliveryError
    available = available_delivery_capabilities()
    if any(available.get(name) != 1 for name in required):
        raise DeliveryError('unsupported_capability')
    return available


def source_commit(project_dir):
    """Resolve the frozen source commit without rejecting a declared overlay."""
    import re
    result = subprocess.run(
        ['git', '-C', str(project_dir), 'rev-parse', '--verify', 'HEAD^{commit}'],
        env={'PATH': os.defpath, 'LC_ALL': 'C', 'GIT_OPTIONAL_LOCKS': '0'},
        capture_output=True, text=True, timeout=5, check=True)
    commit = result.stdout.strip()
    if re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', commit) is None:
        raise ValueError('source_revision_unavailable')
    return commit


def control_metadata(entry=None):
    """Control revision is separate from the application revision."""
    try:
        commit = source_commit(Path(__file__).resolve().parents[2])
    except (OSError, ValueError, subprocess.SubprocessError):
        commit = None
    return {'source_commit': commit,
            'source_runtime_revision': _remote._remote_mcp_runtime_revision(),
            'installed_controller_runtime_revision': ((entry or {}).get('mcp_service') or {}).get('runtime_revision'),
            'capability_versions': require_delivery_capabilities()}


def registered_host_digest(entry, remote_name, home=None):
    """Use the hosting owner's existing nonsecret registered-target binding."""
    ssh = _remote.remote_ssh_parts(entry)
    control_url = entry.get('control_url')
    if isinstance(control_url, str):
        control_url = control_url.strip().rstrip('/')
    return canonical_digest({
        'remote': remote_name,
        'ssh': {'target': ssh['target'], 'host': ssh['host'], 'port': ssh.get('port')},
        'control_transport': entry.get('control_transport') or (
            'tailscale' if entry.get('tailscale_host') else 'https'),
        'control_url': control_url,
        'tailscale_host': (str(entry['tailscale_host']).strip().lower()
                           if entry.get('tailscale_host') else None),
        'mcp_port': int(entry.get('mcp_port') or _remote.DEFAULT_MCP_PORT),
        'runtime_home': str(home if home is not None else
                            _remote.resolve_sandbox_home(entry)).rstrip('/') or '/',
    })


def target_for_project(project_dir, remote_name, *, environment=None, label=None,
                       target_kind=None):
    """Resolve logical selectors without contacting the remote or opening state."""
    root = Path(project_dir).expanduser().resolve()
    selected_label = label or 'default'
    identity = project_identity({'root': root}, label=selected_label)
    kind = target_kind or ('hosted' if environment is not None else
                           'preview' if selected_label.startswith('preview-') else 'deploy')
    runtime_identity = None
    if kind == 'hosted':
        from sandbox.core import _hosting
        validated = _hosting.validate_manifest(root, environment)
        root = Path(validated['project_root']).resolve()
        identity = project_identity({'root': root})
        runtime_identity = canonical_digest({
            'project': validated['project'], 'environment': validated['environment'],
            'compose_project': _hosting.compose_project_name(validated)})
    return validate_target({
        'schema_version': 1, 'project_identity': identity['identity'],
        'project_root_digest': 'sha256:' + hashlib.sha256(str(root).encode()).hexdigest(),
        'target_kind': kind, 'remote_name': remote_name, 'machine_identity': None,
        'registered_host_digest': None, 'environment': environment,
        'label': None if kind == 'hosted' else selected_label,
        'instance_id': None, 'instance_incarnation_id': None,
        'workspace_id': None, 'runtime_identity': runtime_identity})


def build_delivery_service():
    from sandbox.hosting.recovery.repository import RecoveryRepository
    from sandbox.hosting.images.activation.status import read_delivery_status
    from sandbox.core import _hosting
    owners = RecoveryRepository()
    resolved = {}
    roots = {}
    live_creation = {}

    def resolve(*, project_dir, remote, environment=None, label=None):
        target = target_for_project(project_dir, remote, environment=environment, label=label)
        roots[target['project_identity']] = str(Path(project_dir).expanduser().resolve())
        if environment is not None:
            validated = _hosting.validate_manifest(project_dir, environment)
            resolved[(target['project_identity'], remote, environment)] = _hosting.state_key(remote, validated)
        return target

    def key_for(target):
        return resolved.get((target['project_identity'], target['remote_name'], target['environment']))

    def admission(target, operation):
        key = key_for(target)
        if key is None:
            return None
        raw = owners.read_delivery_projection(key, (operation or {}).get('request_id'))
        return normalize_recovery_projection(raw, target, operation)

    def authority(target, operation):
        key = key_for(target)
        if key is None:
            return None
        if operation is None or operation['kind'] == 'immutable_activation':
            raw = read_delivery_status(owners, key, (operation or {}).get('request_id'))
            return normalize_activation_projection(raw, target, operation)
        raw = owners.read_delivery_projection(key, operation.get('request_id'))
        if operation.get('job_id'):
            from sandbox.jobs.registry import read_delivery_job_evidence
            block = normalize_job_projection(read_delivery_job_evidence(
                RUNTIME_DIR / 'jobs' / 'registry.sqlite3', operation['job_id']), target, operation)
            block.setdefault('references', []).extend(
                {'kind': 'recovery_result', 'identifier': item['request_id'], 'digest': item['request_digest']}
                for item in raw.get('recovery_results', [])[-8:])
            return evidence(block)
        return normalize_recovery_projection(raw, target, operation, source_kind='authority')

    def base(kind, target, operation, *, state='partial', result='unknown', code='partial'):
        return {'source_kind': kind, 'observed_at': now(), 'target_digest': canonical_digest(target),
                'applicability': 'required', 'state': state, 'result': result,
                'reason': {'code': code, 'message': 'Current read-only evidence.'},
                'operation_id': (operation or {}).get('operation_id'),
                'request_id': (operation or {}).get('request_id'), 'job_id': (operation or {}).get('job_id')}

    def current_entry(target):
        from sandbox.resources.context import authenticated_target_identity
        entry = _remote.get_remote(target['remote_name'])
        if not isinstance(entry, dict):
            raise ValueError('target_unavailable')
        identity = authenticated_target_identity(target['remote_name'])['target_identity']
        if target['machine_identity'] is not None and target['machine_identity'] != identity:
            raise ValueError('binding_mismatch')
        if (target['registered_host_digest'] is not None and
                target['registered_host_digest'] != registered_host_digest(entry, target['remote_name'])):
            raise ValueError('binding_mismatch')
        return entry

    def stored_creation(target, operation):
        return (operation or {}).get('creation')

    def creation(target, operation):
        if target['target_kind'] == 'hosted' or operation is None:
            return None
        context = (operation.get('creation') or {}).get('creation_context')
        if context is None:
            return base('creation', target, operation)
        entry = current_entry(target)
        root = roots[target['project_identity']]
        result = _remote.read_remote_creation_receipt(entry, _remote.deploy_target_path(entry, root),
            target['label'], creation_context=context,
            expected_incarnation=(operation.get('creation') or {}).get('instance_incarnation_id'))
        block = normalize_creation_receipt(result.get('creation_receipt'), target, operation,
                                           creation_context=context)
        live_creation[operation['operation_id']] = block
        return block

    def runtime(target, operation):
        if target['target_kind'] != 'hosted':
            # Retained creation completion describes ensure, not live process health.
            block = base('runtime', target, operation)
            proof = live_creation.get((operation or {}).get('operation_id'))
            if proof:
                block['instance_incarnation_id'] = proof.get('instance_incarnation_id')
                block['references'] = proof.get('references', [])
            return block
        from sandbox.commands.hosting import _host_runtime_status
        root = roots[target['project_identity']]
        validated = _hosting.validate_manifest(root, target['environment'])
        status = _host_runtime_status(validated, current_entry(target), target['remote_name'], owners.load())
        block = base('runtime', target, operation)
        ready = status['health']['state'] == 'ready' and status['topology']['state'] == 'ready'
        block['result'] = 'passed' if ready else 'failed' if status['health']['state'] == 'degraded' else 'unknown'
        block['application_revision'] = status.get('observed_runtime_revision')
        block['services'] = [item['service'] for item in status['services']][:32]
        block['generation'] = status['generation']
        # Do not claim the configured revision was observed or that a current
        # health probe re-proves an earlier initializer decision.
        block['initializer_result'] = 'unknown'
        return evidence(block)

    def routes(target, operation):
        if operation is None:
            return None
        prepared = (operation.get('routes') or {}).get('route_contract')
        if prepared is None:
            return base('routes', target, operation, state='unsupported', code='unsupported')
        current_entry(target)
        from .routes import observe_routes
        result = observe_routes(prepared)
        block = base('routes', target, operation, state='known' if result['result'] != 'incomplete' else 'partial',
                     result='passed' if result['result'] == 'verified' else 'failed' if result['result'] == 'failed' else 'unknown')
        block.update(route_contract=prepared, route_observation=result, contract_digest=prepared['contract_digest'])
        proof = live_creation.get(operation['operation_id'])
        if proof and proof['state'] == 'known':
            block['instance_incarnation_id'] = proof.get('instance_incarnation_id')
        else:
            block['state'] = 'partial'
        return evidence(block)

    return DeliveryService(DeliveryRepository(RUNTIME_DIR.parent), target_resolver=resolve,
        owner_readers={'admission': admission, 'authority': authority, 'creation': stored_creation},
        current_readers={'creation': creation, 'runtime': runtime, 'routes': routes})
