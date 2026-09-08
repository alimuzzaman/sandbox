"""Finite Linux proof for stopping an owned disposable restart loop."""

import argparse
import base64
import inspect
import io
import json
import re
import subprocess
import tarfile
import time
import uuid

from sandbox.hosting.images.activation import private_containment
from sandbox.hosting.images.activation.private_graph import graph_command_port
from tests.subprocess_support import run_test_process


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    assert re.fullmatch(r'(?:[^\s]+@)?sha256:[a-f0-9]{64}', args.image)
    command, deadline = graph_command_port({'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
                                           'LANG': 'C', 'LC_ALL': 'C'}, 180)
    project = 'sb-containment-canary-' + uuid.uuid4().hex
    volume = project + '-data'
    identity = None; created_volume = False
    try:
        daemon = command(['docker', 'info', '--format', '{{.ID}}'], max_output_bytes=4096).decode().strip()
        command(['docker', 'volume', 'create', '--label', 'com.docker.compose.project=' + project, volume])
        created_volume = True
        identity = command(['docker', 'create', '--pull', 'never', '--network', 'none', '--restart', 'always',
            '--label', 'com.docker.compose.project=' + project,
            '--label', 'com.docker.compose.service=probe',
            '--mount', 'type=volume,source=' + volume + ',target=/canary', '--entrypoint', '/bin/sh',
            args.image, '-c', 'printf preserved > /canary/preserved; exit 1']).decode().strip()
        assert re.fullmatch(r'[a-f0-9]{64}', identity)
        command(['docker', 'start', identity])
        frame = {'target': {'machine_identity': 'disposable-canary', 'target_identity': project,
                            'daemon_identity': daemon}, 'compose_project': project,
                 'transaction_digest': 'sha256:' + 'a' * 64, 'generation': 0,
                 'binding_key': base64.b64encode(b'k' * 32).decode(),
                 'operation': 'plan', 'containers': []}
        program = inspect.getsource(private_containment) + '\n' + inspect.getsource(graph_command_port) + '\nmain()\n'
        def invoke(value):
            result = run_test_process(['sudo', '-n', 'python3', '-c', program],
                input=json.dumps(value).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C'},
                timeout=100, check=False)
            assert result.returncode == 0 and not result.stderr
            assert len(result.stdout) < 65536
            return json.loads(result.stdout)
        while True:
            row = json.loads(command(['docker', 'inspect', identity]))[0]
            state = row['State']
            if (state.get('Restarting') is True and state.get('Running') is True
                    # Leave enough backoff for both plans and all pre-write
                    # observations; early subsecond waits can legitimately
                    # change before apply reaches its first write.
                    and state.get('Pid') == 0 and row.get('RestartCount', 0) >= 9):
                planned = invoke(frame)
                if planned.get('code') == 'planned': break
                assert planned.get('code') in {'evidence_changed', 'container_state_invalid'}, planned
            assert time.monotonic() < deadline - 60, 'restart wait unavailable'
            time.sleep(0.1)
        assert len(planned['containers']) == 1
        assert planned['containers'][0]['container_id'] == identity
        assert planned['containers'][0]['running'] is True
        result = invoke({**frame, 'operation': 'apply', 'containers': planned['containers']})
        assert result.get('code') == 'contained', result
        after = json.loads(command(['docker', 'inspect', identity]))[0]
        assert after['Id'] == identity and after['State']['Running'] is False
        assert after['State']['Restarting'] is False and after['State']['Pid'] == 0
        assert after['HostConfig']['RestartPolicy']['Name'] == 'no'
        assert any(m.get('Name') == volume and m.get('Destination') == '/canary' for m in after['Mounts'])
        content = command(['docker', 'cp', identity + ':/canary/preserved', '-'], max_output_bytes=16384)
        with tarfile.open(fileobj=io.BytesIO(content), mode='r:') as archive:
            member = archive.getmembers()[0]
            assert member.isfile() and member.size == len(b'preserved')
            assert archive.extractfile(member).read() == b'preserved'
        print(json.dumps({'ok': True, 'code': 'containment_restart_canary_passed',
                          'original_container_stopped': True, 'restart_disabled': True,
                          'volume_data_preserved': True}))
    finally:
        if identity:
            row = json.loads(command(['docker', 'inspect', identity]))[0]
            assert row['Id'] == identity and row['Config']['Labels']['com.docker.compose.project'] == project
            command(['docker', 'update', '--restart=no', identity])
            if row['State']['Running']:
                command(['docker', 'stop', '--time', '2', identity])
            # The pinned image may declare anonymous volumes. These were
            # created only for this owned fixture; named volumes are separate.
            command(['docker', 'rm', '-v', identity])
        if created_volume:
            row = json.loads(command(['docker', 'volume', 'inspect', volume]))[0]
            assert row['Name'] == volume and row['Labels']['com.docker.compose.project'] == project
            command(['docker', 'volume', 'rm', volume])


if __name__ == '__main__':
    main()
