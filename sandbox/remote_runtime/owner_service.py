"""Read-only selected-unit proof before joined owner lifecycle staging."""
import base64
import json
import shlex

from sandbox.resources.host_memory.models import HEX24


class OwnerServiceUnavailable(RuntimeError):
    code = 'owner_service_identity_unavailable'


PROGRAM = r'''
import base64,hashlib,json,os,pathlib,pwd,re,shlex,stat,sys
try:
    expected=json.loads(base64.b64decode(sys.argv[1]))
    uid=os.geteuid(); home=pathlib.Path(pwd.getpwuid(uid).pw_dir).resolve()
    unit=home/'.config/systemd/user'/expected['service_name']
    info=unit.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_uid==uid and not info.st_mode&0o022
    fields={}
    for line in unit.read_text().splitlines():
        key,sep,value=line.partition('=')
        if sep and key in ('ExecStart','WorkingDirectory','EnvironmentFile'):
            assert key not in fields; fields[key]=value
        for name in ('SANDBOX_REMOTE_MCP_MARKER','SANDBOX_REMOTE_MCP_RUNTIME_REVISION'):
            prefix='Environment='+name+'='
            if line.startswith(prefix):
                assert name not in fields; fields[name]=line[len(prefix):]
    assert fields['SANDBOX_REMOTE_MCP_MARKER']==expected['ownership_marker']
    assert fields['SANDBOX_REMOTE_MCP_RUNTIME_REVISION']==expected['runtime_revision']
    root=home/'sandbox/sb-src'
    command=shlex.split(fields['ExecStart'].replace('%h',str(home)))
    assert command==[str(root/'sb'),'mcp','--transport','streamable-http','--bind',expected['bind'],'--port',str(expected['port'])]
    assert fields['WorkingDirectory'].replace('%h',str(home))==str(root)
    assert fields['EnvironmentFile']==expected['environment_file']
    credential=pathlib.Path(fields['EnvironmentFile'].replace('%h',str(home)))
    private=credential.lstat()
    assert stat.S_ISREG(private.st_mode) and private.st_uid==uid and stat.S_IMODE(private.st_mode)==0o600
    machine=pathlib.Path('/etc/machine-id').read_text().strip().lower()
    assert re.fullmatch('[0-9a-f]{32}',machine)
    identity=hashlib.sha256(b'sandbox-host-machine-id-v1\0'+machine.encode('ascii')).hexdigest()[:24]
    assert identity==expected['target_identity']
    print(json.dumps({'schema_version':1,'ok':True,'ownership':'proven','owner_uid':uid,
      'target_identity':identity,'ownership_marker':expected['ownership_marker'],
      'runtime_revision':expected['runtime_revision'],'credential_state':'private-file-present'}))
except Exception:
    print(json.dumps({'schema_version':1,'ok':False,'code':'owner_service_identity_unavailable'}))
    raise SystemExit(1)
'''


def probe_joined_owner_service(record, bind, port, ssh_run, *, timeout=15):
    from sandbox.core._remote import REMOTE_MCP_SERVICE, _REMOTE_MCP_UNIT_ENV
    service, machine = record.get('mcp_service'), record.get('control_identity')
    if (record.get('joined') is not True or record.get('control_transport') != 'tailscale'
            or not isinstance(service, dict) or not isinstance(machine, dict)
            or type(machine.get('schema_version')) is not int or machine['schema_version'] != 1
            or any(not isinstance(value, str) or not HEX24.fullmatch(value) for value in
                (service.get('ownership_marker'), service.get('runtime_revision'), machine.get('target_identity')))):
        raise OwnerServiceUnavailable('existing same-machine owner service pins are unavailable')
    expected = {'service_name': REMOTE_MCP_SERVICE, 'environment_file': _REMOTE_MCP_UNIT_ENV,
        'ownership_marker': service['ownership_marker'], 'runtime_revision': service['runtime_revision'],
        'target_identity': machine['target_identity'], 'bind': bind, 'port': port}
    payload = base64.b64encode(json.dumps(expected, sort_keys=True, separators=(',', ':')).encode()).decode()
    result = ssh_run(record, 'python3 -B -c ' + shlex.quote(PROGRAM) + ' ' + shlex.quote(payload), timeout=timeout)
    try:
        observed = json.loads(result.stdout)
    except (ValueError, TypeError):
        observed = None
    if (result.returncode != 0 or not isinstance(observed, dict)
            or set(observed) != {'schema_version', 'ok', 'ownership', 'owner_uid', 'target_identity',
                                  'ownership_marker', 'runtime_revision', 'credential_state'}
            or observed.get('ok') is not True
            or type(observed.get('schema_version')) is not int or observed['schema_version'] != 1
            or observed.get('ownership') != 'proven'
            or type(observed.get('owner_uid')) is not int or observed['owner_uid'] < 0
            or observed.get('credential_state') != 'private-file-present'
            or any(observed.get(field) != expected[field] for field in
                ('target_identity', 'ownership_marker', 'runtime_revision'))):
        raise OwnerServiceUnavailable('selected unit ownership or private credential-file metadata is unproven')
    return observed
