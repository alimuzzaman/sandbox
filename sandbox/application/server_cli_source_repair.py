"""Fixed installed source repair transport, separate from durable execution."""
from __future__ import annotations

import json
import re
import shlex

from sandbox.application.server_cli import read_current_server_owner, ServerCLIUnavailable
from sandbox.application.server_cli_delivery import Budget, owner_projection, closed_json
from sandbox.hosting.source_repair import CONTRACT


def unavailable():
    raise ServerCLIUnavailable('rendered source repair is unavailable')


def _request(value):
    if (type(value) is not dict or set(value) != {'schema_version', 'contract', 'owner', 'remote', 'name', 'source', 'confirm', 'plan_digest'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['contract'] != CONTRACT or type(value['confirm']) is not bool
            or value['source'] != 'deployed' or type(value['owner']) is not dict
            or set(value['owner']) != {'target_identity', 'ownership_marker', 'runtime_revision'}
            or any(not isinstance(item, str) or re.fullmatch('[0-9a-f]{24}', item) is None for item in value['owner'].values())
            or not isinstance(value['remote'], str) or re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', value['remote']) is None
            or not isinstance(value['name'], str) or re.fullmatch('[a-z0-9][a-z0-9-]{0,62}', value['name']) is None
            or (value['confirm'] and (not isinstance(value['plan_digest'], str) or re.fullmatch('sha256:[0-9a-f]{64}', value['plan_digest']) is None))
            or (not value['confirm'] and value['plan_digest'] is not None)):
        unavailable()
    return value


def _response(value, request):
    fields = {'schema_version', 'ok', 'contract', 'status', 'plan_digest', 'name', 'commit', 'owner'}
    if (type(value) is not dict or type(value.get('ok')) is not bool
            or value.get('ok') is not True or value.get('contract') != CONTRACT
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('owner') != request['owner'] or value.get('name') != request['name']
            or not isinstance(value.get('plan_digest'), str) or re.fullmatch('sha256:[0-9a-f]{64}', value['plan_digest']) is None
            or not isinstance(value.get('commit'), str) or re.fullmatch('[0-9a-f]{40}', value['commit']) is None):
        unavailable()
    if request['confirm']:
        if (set(value) != fields | {'manifest_digest', 'tree_digest', 'orphan_retained'}
                or value['status'] != 'repaired' or value['plan_digest'] != request['plan_digest']
                or value['orphan_retained'] is not True
                or any(not isinstance(value[key], str) or re.fullmatch('sha256:[0-9a-f]{64}', value[key]) is None
                       for key in ('manifest_digest', 'tree_digest'))):
            unavailable()
    elif set(value) != fields or value['status'] != 'planned':
        unavailable()
    return value


def contract_available():
    from sandbox.application.command_selection import selected_invocation, CommandSelectionUnavailable
    try:
        return selected_invocation(('host', 'stack', 'repair-source', '--installed-source-repair={}')) is not None
    except CommandSelectionUnavailable:
        return False


def dispatch_if_selected(args):
    if getattr(args, 'action', None) != 'stack' or getattr(args, 'image_action', None) != 'repair-source':
        if getattr(args, 'installed_source_repair', None) is not None:
            unavailable()
        return False
    request = None
    started = False
    packed = getattr(args, 'installed_source_repair', None)
    try:
        if packed is not None:
            # A fixed receiver admits no extra ordinary host options.
            expected = ['host', 'stack', 'repair-source', '--installed-source-repair', packed, '--json']
            actual = list(getattr(args, '_server_cli_argv', ()))
            if actual[:1] == ['--strict-runtime']:
                actual.pop(0)
            if actual != expected or not isinstance(packed, str) or len(packed.encode()) > 2048:
                unavailable()
            request = _request(closed_json(packed))
            if owner_projection(read_current_server_owner()) != request['owner']:
                unavailable()
            from sandbox.hosting.source_repair import repair_rendered_source
            started = request['confirm']
            payload = repair_rendered_source(remote_name=request['remote'], name=request['name'],
                source=request['source'], confirm=request['confirm'], plan_digest=request['plan_digest'],
                expected_owner=request['owner'])
            payload['owner'] = request['owner']
            _response(payload, request)
        else:
            # Closed public grammar. Options accepted by other host actions do
            # not become authority for this metadata-only operation.
            values = list(getattr(args, '_server_cli_argv', ()))
            if values[:1] == ['--strict-runtime']:
                values.pop(0)
            if values[:3] != ['host', 'stack', 'repair-source']:
                unavailable()
            values = values[3:]
            permitted = {'--remote', '--name', '--repair-source', '--repair-plan-digest'}
            switches = {'--plan', '--confirm', '--json'}
            seen = set()
            while values:
                option = values.pop(0)
                if option in seen or option not in permitted | switches:
                    unavailable()
                seen.add(option)
                if option in permitted:
                    if not values or values[0].startswith('--'):
                        unavailable()
                    values.pop(0)
            if bool(getattr(args, 'plan', False)) == bool(getattr(args, 'confirm', False)):
                unavailable()
            from sandbox.core import _remote
            from sandbox.resources.context import authenticated_target_identity
            budget = Budget(135)
            remote = getattr(args, 'remote', None)
            identity = authenticated_target_identity(remote, budget_seconds=budget.remaining())
            record = _remote.get_remote(remote, budget_seconds=budget.remaining())
            if (not isinstance(record, dict) or record.get('joined') is not True
                    or record.get('control_transport') != 'tailscale'
                    or (record.get('control_identity') or {}).get('target_identity') != identity['target_identity']):
                unavailable()
            service = record.get('mcp_service') or {}
            owner = {'target_identity': identity['target_identity'], 'ownership_marker': service.get('ownership_marker'),
                     'runtime_revision': service.get('runtime_revision')}
            request = _request({'schema_version': 1, 'contract': CONTRACT, 'owner': owner, 'remote': remote,
                'name': getattr(args, 'name', None), 'source': getattr(args, 'repair_source', None),
                'confirm': bool(getattr(args, 'confirm', False)), 'plan_digest': getattr(args, 'repair_plan_digest', None)})
            def control(argv, maximum, seconds):
                result = _remote.ssh_run_bounded(record, shlex.join([_remote.remote_sb_path(record), *argv, '--json']),
                    timeout=min(seconds, budget.remaining()), max_output=maximum, terminate_on_output_limit=True)
                budget.check()
                if (result.returncode or result.termination_reason or result.stdout_truncated or result.stderr_truncated
                        or not isinstance(result.stdout, str) or len(result.stdout.encode()) > maximum):
                    unavailable()
                return closed_json(result.stdout)
            capabilities = control(['job-capabilities'], 16384, 15)
            if (not isinstance(capabilities, dict) or capabilities.get('ok') is not True
                    or any(capabilities.get(key) != value for key, value in owner.items())
                    or CONTRACT not in capabilities.get('source_repair_capabilities', [])):
                unavailable()
            started = request['confirm']
            payload = control(['host', 'stack', 'repair-source', '--installed-source-repair',
                json.dumps(request, sort_keys=True, separators=(',', ':'))], 4096, 125)
            _response(payload, request)
    except Exception:
        payload = {'schema_version': 1, 'ok': False, 'contract': CONTRACT,
            'status': 'repair_unconfirmed' if started else 'unavailable',
            'plan_digest': request['plan_digest'] if request is not None else None,
            'error': 'Source repair is unavailable or incomplete; preserve the exact plan and original source.'}
    print(json.dumps(payload, sort_keys=True) if getattr(args, 'json', False) else
          'Rendered source: ' + payload['status'] + (' / plan ' + payload['plan_digest'] if payload.get('plan_digest') else ''))
    if not payload['ok']:
        raise SystemExit(1)
    return True
