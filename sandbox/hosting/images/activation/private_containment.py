"""Exact-container containment; no removal, volume operation or implicit restart."""
import base64
import hashlib
import hmac
import json
import re
import time
from pathlib import Path

_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}


def _process_binding(pid, container_id):
    root = Path('/proc') / str(pid)
    cgroup = (root / 'cgroup').read_bytes()
    if container_id.encode() not in cgroup: raise ValueError('owner_unavailable')
    value = (root / 'stat').read_bytes(); tail = value[value.rfind(b')') + 2:].split()
    if len(tail) < 20: raise ValueError('owner_unavailable')
    return {'pid': pid, 'started': int(tail[19]), 'cgroup_digest': hashlib.sha256(cgroup).hexdigest()}


def snapshot(frame, command):
    project = frame['compose_project']; target = frame['target']
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,127}', project): raise ValueError('evidence_changed')
    daemon = command(['docker', 'info', '--format', '{{.ID}}'], max_output_bytes=4096).decode().strip()
    if daemon != target['daemon_identity']: raise ValueError('evidence_changed')
    ids = sorted(command(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + project], max_output_bytes=16384).decode().split())
    if len(ids) > 128 or len(set(ids)) != len(ids) or any(not re.fullmatch(r'[a-f0-9]{64}', value) for value in ids):
        raise ValueError('evidence_changed')
    rows = json.loads(command(['docker', 'inspect', *ids], max_output_bytes=8 * 1024 * 1024)) if ids else []
    if sorted(row.get('Id', '') for row in rows) != ids: raise ValueError('evidence_changed')
    key = base64.b64decode(frame['binding_key'], validate=True)
    if len(key) != 32: raise ValueError('evidence_changed')
    result = []
    for row in sorted(rows, key=lambda row: row['Id']):
        if row.get('Config', {}).get('Labels', {}).get('com.docker.compose.project') != project:
            raise ValueError('evidence_changed')
        state = row['State']; pid = state.get('Pid')
        if state.get('Paused') or state.get('Restarting') or type(pid) is not int or pid < 0:
            raise ValueError('owner_unavailable')
        running = state.get('Running')
        if type(running) is not bool or running != (pid > 0): raise ValueError('owner_unavailable')
        process = _process_binding(pid, row['Id']) if pid else None
        policy = row.get('HostConfig', {}).get('RestartPolicy')
        if not isinstance(policy, dict) or policy.get('Name') not in {'', 'no', 'always', 'unless-stopped', 'on-failure'}:
            raise ValueError('evidence_changed')
        private = {'image': row['Image'], 'mounts': row.get('Mounts'), 'labels': row.get('Config', {}).get('Labels'),
            'process': process, 'restart': policy, 'running': state.get('Running'), 'status': state.get('Status')}
        binding = 'sha256:' + hmac.new(key, b'sandbox-containment-v1\0' + json.dumps(private, sort_keys=True, separators=(',', ':')).encode(), hashlib.sha256).hexdigest()
        result.append({'container_id': row['Id'], 'binding_digest': binding,
            'restart_policy': policy, 'running': state.get('Running')})
    return result


def main():
    import sys
    try:
        payload = sys.stdin.buffer.read(65537)
        if len(payload) > 65536: raise ValueError('evidence_changed')
        frame = json.loads(payload)
        if set(frame) != {'target', 'compose_project', 'transaction_digest', 'generation', 'binding_key', 'operation', 'containers'}:
            raise ValueError('evidence_changed')
        if frame['operation'] not in {'plan', 'apply'}: raise ValueError('evidence_changed')
        command, deadline = graph_command_port(_ENV, 240)
        before = snapshot(frame, command)
        if frame['operation'] == 'apply':
            if before != frame['containers']: raise ValueError('evidence_changed')
            for row in before:
                if time.monotonic() >= deadline: raise ValueError('acceptance_unknown')
                # Recheck the same target immediately before each write. Completed
                # rows may have changed, so compare the selected row only.
                current = {item['container_id']: item for item in snapshot(frame, command)}
                if current.get(row['container_id']) != row: raise ValueError('evidence_changed')
                if row['restart_policy']['Name'] not in {'', 'no'}:
                    command(['docker', 'update', '--restart=no', row['container_id']], max_output_bytes=4096)
                if row['running']:
                    command(['docker', 'stop', '--time', '30', row['container_id']], max_output_bytes=4096)
            after = snapshot(frame, command)
            if ([row['container_id'] for row in after] != [row['container_id'] for row in before]
                    or any(row['running'] or row['restart_policy']['Name'] not in {'', 'no'} for row in after)):
                raise ValueError('acceptance_unknown')
            result = {'ok': True, 'code': 'contained', 'containers': after}
        else:
            if snapshot(frame, command) != before: raise ValueError('evidence_changed')
            result = {'ok': True, 'code': 'planned', 'containers': before}
    except Exception as exc:
        result = {'ok': False, 'code': str(exc) if str(exc) in {'owner_unavailable', 'evidence_changed'} else 'acceptance_unknown'}
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(',', ':')) + '\n')
