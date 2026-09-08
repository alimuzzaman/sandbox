"""Installed, bounded PostgreSQL capture and disposable restore helper.

Only the registered recovery transport invokes this helper. stdin carries a
closed request and optional broker material; stdout is fixed metadata or one
private capture archive. No source rows, connection strings or errors are logged.
"""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import stat
import sys
import tarfile
import tempfile
import time
from urllib.parse import parse_qs, unquote, urlsplit

# This helper is copied as source by the registered transport. Keep it stdlib-only.
ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin", "LANG": "C.UTF-8"}
MAX_ARCHIVE = 512 * 1024 * 1024


def run(argv, *, data=None, timeout=60, output=None):
    result = subprocess.run(argv, input=data, stdout=output or subprocess.PIPE,
        stderr=subprocess.DEVNULL, env=ENV, timeout=timeout, check=False)
    if result.returncode != 0:
        raise ValueError("operation_failed")
    if output is None and len(result.stdout) > MAX_ARCHIVE:
        raise ValueError("output_bound")
    return result.stdout


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def private_write(path, data):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data); handle.flush(); os.fsync(handle.fileno())


def owned_read(path, maximum):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > maximum):
            raise ValueError('path_unsafe')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum: raise ValueError('path_unsafe')
        return data
    finally:
        os.close(descriptor)


def inspect_source(source):
    value = json.loads(run(["docker", "inspect", source["container_id"]]))
    if len(value) != 1:
        raise ValueError("source_changed")
    value = value[0]
    labels = value.get("Config", {}).get("Labels") or {}
    application_source = source.get("profile") in {"lenzora-prod-legacy", "lenzora-prod-storage"}
    mount_path = "/app/storage" if application_source else "/var/lib/postgresql/data"
    if (value["Id"] != source["container_id"] or value["Image"] != source["image_id"]
            or labels.get("com.docker.compose.project") != source["compose_project"]
            or (not application_source and not value.get("State", {}).get("Running"))
            or not any(m.get("Type") == "volume" and m.get("Name") == source["volume"]
                and m.get("Destination") == mount_path for m in value.get("Mounts", []))):
        raise ValueError("source_changed")
    return {"container_id": value["Id"], "image_id": value["Image"], "volume": source["volume"]}


def sql(client, database, query):
    return run([*client, "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "--dbname", database],
        data=query.encode(), timeout=300).decode().strip()


def observation(client, database, prefix=""):
    # Identifiers come only from pg_catalog and are quoted by format('%I').
    # CREATE is forbidden inside READ ONLY, even for temporary tables. Create
    # the session-local accumulator before importing the read-only snapshot.
    query = 'CREATE TEMP TABLE recovery_counts (name text, count bigint);\n' + prefix + r'''
DO $body$ DECLARE item record; amount bigint; BEGIN
 FOR item IN SELECT schemaname, tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename LOOP
 EXECUTE format('SELECT count(*) FROM %I.%I',item.schemaname,item.tablename) INTO amount;
 INSERT INTO recovery_counts VALUES(item.tablename,amount);
 END LOOP;
END $body$;
SELECT json_build_object(
 'major', current_setting('server_version_num')::int/10000,
 'database_identity', md5(current_database()),
 'tables', (SELECT coalesce(json_agg(json_build_object('name',name,'count',count) ORDER BY name),'[]') FROM recovery_counts),
 'constraints', (SELECT coalesce(json_agg(json_build_object('table',c.relname,'name',con.conname,'definition',pg_get_constraintdef(con.oid),'validated',con.convalidated) ORDER BY c.relname,con.conname),'[]') FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
 'columns', (SELECT coalesce(json_agg(json_build_object('table',table_name,'column',column_name,'type',data_type,'nullable',is_nullable) ORDER BY table_name,ordinal_position),'[]') FROM information_schema.columns WHERE table_schema='public'));
'''
    # Writes touch only the already-created session-local temporary table.
    raw = json.loads(sql(client, database, query))
    migrations = sql(client, database, prefix + r'''
SELECT CASE WHEN to_regclass('public._prisma_migrations') IS NULL THEN 'absent' ELSE 'present' END;
''')
    checksum = "absent"
    if migrations == "present":
        checksum = sql(client, database, prefix + '''SELECT md5(coalesce(string_agg(migration_name || ':' || checksum || ':' || (finished_at IS NOT NULL)::text || ':' || (rolled_back_at IS NOT NULL)::text, ',' ORDER BY migration_name),'')) FROM public._prisma_migrations;''')
    return {"major": raw["major"], "database_identity": raw["database_identity"],
        "table_counts": raw["tables"], "migration_checksum": checksum,
        "schema_digest": "sha256:" + hashlib.sha256(canonical({"constraints": raw["constraints"], "columns": raw["columns"]})).hexdigest(),
        "constraints_valid": all(row["validated"] for row in raw["constraints"])}


def local_client(source):
    return ["docker", "exec", "-i", "--user", "postgres", "-e", "PGUSER=" + source["role"], source["container_id"]]


def external_client(source, credential, work, name):
    uri = urlsplit(credential.decode())
    if uri.scheme not in {"postgres", "postgresql"} or not uri.hostname or not uri.username or not uri.password:
        raise ValueError("credential_invalid")
    database = unquote(uri.path.lstrip('/'))
    if database != source["database"] or unquote(uri.username) != source["role"]:
        raise ValueError("credential_binding_changed")
    query = parse_qs(uri.query)
    sslmode = query.get("sslmode", ["verify-full"])
    if len(sslmode) != 1 or sslmode[0] not in {"require", "verify-ca", "verify-full"}:
        raise ValueError("credential_invalid")
    fields = [uri.hostname, str(uri.port or 5432), database, unquote(uri.username), unquote(uri.password)]
    if any(any(c in field for c in '\n\r\0') for field in fields):
        raise ValueError("credential_invalid")
    password = ':'.join(field.replace('\\', '\\\\').replace(':', '\\:') for field in fields) + '\n'
    # Credentials remain in the owned client container's private tmpfs.
    run(["docker", "create", "--name", name, "--label", "sandbox.recovery.owner=" + name,
        "--network", "bridge", "--tmpfs", "/run/recovery:rw,noexec,nosuid,mode=0700",
        "--entrypoint", "sleep", source["client_image_id"], "3600"])
    run(["docker", "start", name])
    run(["docker", "exec", "-i", name, "sh", "-c", "umask 077; cat > /run/recovery/pgpass"], data=password.encode())
    return ["docker", "exec", "-i", "-e", "PGPASSFILE=/run/recovery/pgpass", "-e", "PGHOST=" + uri.hostname,
        "-e", "PGPORT=" + str(uri.port or 5432), "-e", "PGUSER=" + unquote(uri.username),
        "-e", "PGSSLMODE=" + sslmode[0], name]


def capture(source, client, work):
    def quiescent():
        return source['profile'] != 'lenzora-prod-legacy' or not bool(run(
            ['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=sandbox-host-lenzora-production']).strip())
    before_quiescent = quiescent()
    hold = subprocess.Popen([*client, "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "--dbname", source["database"]],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=ENV)
    try:
        hold.stdin.write(b"BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT pg_export_snapshot();\n"); hold.stdin.flush()
        import select
        if not select.select([hold.stdout], [], [], 30)[0]:
            raise ValueError("snapshot_unavailable")
        snapshot = hold.stdout.readline(256).decode().strip()
        if not re.fullmatch(r"[0-9A-Fa-f-]{1,128}", snapshot):
            raise ValueError("snapshot_unavailable")
        prefix = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET TRANSACTION SNAPSHOT '" + snapshot + "';\n"
        evidence = observation(client, source["database"], prefix)
        dump = work / "database.dump"
        with dump.open("xb") as output:
            os.chmod(dump, 0o600)
            run([*client, "pg_dump", "--format=custom", "--no-owner", "--no-acl", "--snapshot", snapshot,
                 "--dbname", source["database"]], timeout=1800, output=output)
        if dump.stat().st_size > MAX_ARCHIVE or dump.read_bytes()[:5] != b"PGDMP":
            raise ValueError("dump_invalid")
        evidence["dump_digest"] = "sha256:" + hashlib.sha256(dump.read_bytes()).hexdigest()
        evidence['captured_at'] = int(time.time())
        evidence['captured_quiescent'] = before_quiescent and quiescent()
        evidence["source_digest"] = "sha256:" + hashlib.sha256(canonical(source)).hexdigest()
        private_write(work / "evidence.json", canonical(evidence))
        archive = work / "capture.tar"
        with tarfile.open(archive, "x") as tar:
            for name in ("database.dump", "evidence.json"):
                tar.add(work / name, arcname=name, recursive=False)
        os.chmod(archive, 0o600)
        return archive
    finally:
        if hold.poll() is None:
            hold.terminate()
            try: hold.wait(timeout=10)
            except subprocess.TimeoutExpired: hold.kill(); hold.wait()


def restore(source, archive, work, name, target_volume=None, target_password=b''):
    with tarfile.open(archive, 'r') as tar:
        members = tar.getmembers()
        if sorted(item.name for item in members) != ['database.dump', 'evidence.json'] or any(
                not item.isfile() or item.size < 1 or item.size > MAX_ARCHIVE for item in members):
            raise ValueError("archive_invalid")
        for member in members:
            private_write(work / member.name, tar.extractfile(member).read())
    evidence = json.loads((work / 'evidence.json').read_bytes())
    dump = work / 'database.dump'
    if evidence['dump_digest'] != 'sha256:' + hashlib.sha256(dump.read_bytes()).hexdigest():
        raise ValueError('dump_changed')
    password = target_password if target_volume is not None else os.urandom(48).hex().encode()
    if not password or len(password) > 4096 or any(c in password for c in (b'\n', b'\r', b'\0')):
        raise ValueError('target_password_invalid')
    volume = target_volume or name + '-data'
    database = 'lenzora' if target_volume is not None else source['database']
    role = 'lenzora' if target_volume is not None else source['role']
    if target_volume is not None:
        if (source['profile'] != 'lenzora-prod-legacy' or target_volume != 'sandbox-host-lenzora-production_lenzora-postgres-data'
                or evidence.get('captured_quiescent') is not True
                or run(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=sandbox-host-lenzora-production']).strip()):
            raise ValueError('production_transfer_not_quiescent')
    # create refuses a pre-existing target before any import; retained uncertainty
    # never turns into an overwrite or a new target name.
    if run(['docker', 'volume', 'ls', '--filter', 'name=^' + volume + '$', '--format', '{{.Name}}']).strip():
        raise ValueError('restore_target_exists')
    run(['docker', 'volume', 'create', '--label', 'sandbox.recovery.owner=' + name, volume])
    run(['docker', 'create', '--name', name, '--label', 'sandbox.recovery.owner=' + name,
         '--network', 'none', '--mount', 'type=volume,source=' + volume + ',target=/var/lib/postgresql/data',
         '--tmpfs', '/run/recovery:rw,noexec,nosuid,mode=0700',
         '-e', 'POSTGRES_HOST_AUTH_METHOD=scram-sha-256', '-e', 'POSTGRES_USER=' + role,
         '-e', 'POSTGRES_DB=' + database, '--entrypoint', 'sleep', source['client_image_id'], '3600'])
    run(['docker', 'start', name])
    run(['docker', 'exec', '-i', name, 'sh', '-c', 'umask 077; cat > /run/recovery/password'], data=password)
    run(['docker', 'exec', '-d', name, 'sh', '-c', 'export POSTGRES_PASSWORD_FILE=/run/recovery/password; exec docker-entrypoint.sh postgres'])
    client = ['docker', 'exec', '-i', '--user', 'postgres', '-e', 'PGUSER=' + role, name]
    deadline = time.monotonic() + 90
    while True:
        try:
            sql(client, database, 'SELECT 1;'); break
        except ValueError:
            if time.monotonic() >= deadline: raise
            time.sleep(1)
    with dump.open('rb') as handle:
        result = subprocess.run([*client, 'pg_restore', '--exit-on-error', '--no-owner', '--no-acl', '--dbname', database],
            stdin=handle, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=ENV, timeout=1800)
    if result.returncode: raise ValueError('restore_failed')
    actual = observation(client, database)
    comparison = set(actual) - ({'database_identity'} if target_volume is not None else set())
    # The schema digest includes each constraint's validation state. Preserve
    # deliberately NOT VALID source constraints without silently validating them.
    if any(actual[key] != evidence[key] for key in comparison):
        raise ValueError('restore_verification_failed')
    run(['docker', 'exec', '--user', 'postgres', name, 'pg_ctl', '-D', '/var/lib/postgresql/data', '-m', 'fast', '-w', 'stop'])
    run(['docker', 'stop', '--time', '30', name])
    return {'schema_version': 1, 'ok': True, 'code': 'restore_verified', 'target': name,
        'volume': volume, 'target_database': database, 'target_role': role,
        'source_database_identity': evidence['database_identity'], 'dump_digest': evidence['dump_digest'], 'observation': actual}


def storage_manifest(archive):
    entries = []
    with tarfile.open(archive, 'r') as tar:
        for item in tar.getmembers():
            name = item.name.removeprefix('./')
            if name in {'', '.'}: continue
            if (name.startswith('/') or '..' in Path(name).parts or not (item.isfile() or item.isdir())
                    or item.size > MAX_ARCHIVE or len(entries) >= 100000):
                raise ValueError('storage_archive_invalid')
            content = hashlib.sha256(tar.extractfile(item).read()).hexdigest() if item.isfile() else None
            entries.append({'path': name, 'size': item.size, 'mode': item.mode, 'content': content, 'directory': item.isdir()})
    if len({item['path'] for item in entries}) != len(entries): raise ValueError('storage_archive_invalid')
    return {'files': sum(not item['directory'] for item in entries), 'bytes': sum(item['size'] for item in entries),
        'file_manifest_digest': 'sha256:' + hashlib.sha256(canonical(sorted(entries, key=lambda row: row['path']))).hexdigest()}


def storage_tar(container, directory, destination):
    with destination.open('xb') as handle:
        os.chmod(destination, 0o600)
        run(['docker', 'exec', '--user', '0', container, 'tar', '-cf', '-', '-C', directory, '.'], timeout=1800, output=handle)
    if destination.stat().st_size > MAX_ARCHIVE: raise ValueError('storage_archive_invalid')
    return storage_manifest(destination)


def storage_operation(source, operation, work, archive_bytes, name):
    if operation in {'observe', 'capture'}:
        client = name + '-reader'
        run(['docker', 'create', '--name', client, '--label', 'sandbox.recovery.owner=' + client,
            '--network', 'none', '--read-only', '--user', '0', '--mount',
            'type=volume,source=' + source['volume'] + ',target=/source,readonly',
            '--entrypoint', 'sleep', source['image_id'], '3600'])
        run(['docker', 'start', client])
        try:
            first = storage_tar(client, '/source', work / 'storage.tar')
            second = storage_tar(client, '/source', work / 'check.tar')
        finally:
            owner = json.loads(run(['docker', 'inspect', client]))[0]
            if owner.get('Config', {}).get('Labels', {}).get('sandbox.recovery.owner') != client:
                raise ValueError('owner_changed')
            run(['docker', 'rm', '-f', owner['Id']])
        if first != second: raise ValueError('storage_changed')
        evidence = {**first, 'captured_at': int(time.time()), 'source_digest': 'sha256:' + hashlib.sha256(canonical(source)).hexdigest(),
            'archive_digest': 'sha256:' + hashlib.sha256((work / 'storage.tar').read_bytes()).hexdigest()}
        result = work / 'result.json'
        if operation == 'observe':
            private_write(result, canonical({'schema_version': 1, 'ok': True, 'code': 'observed', 'observation': evidence}))
            return result
        private_write(work / 'evidence.json', canonical(evidence))
        result = work / 'capture.tar'
        with tarfile.open(result, 'x') as tar:
            for filename in ('storage.tar', 'evidence.json'): tar.add(work / filename, arcname=filename, recursive=False)
        os.chmod(result, 0o600)
        return result
    private_write(work / 'input.tar', archive_bytes)
    with tarfile.open(work / 'input.tar', 'r') as tar:
        members = tar.getmembers()
        if sorted(item.name for item in members) != ['evidence.json', 'storage.tar'] or any(
                not item.isfile() or item.size < 1 or item.size > MAX_ARCHIVE for item in members):
            raise ValueError('storage_archive_invalid')
        for item in members: private_write(work / item.name, tar.extractfile(item).read())
    evidence = json.loads((work / 'evidence.json').read_bytes())
    archive = work / 'storage.tar'
    expected = storage_manifest(archive)
    if evidence['archive_digest'] != 'sha256:' + hashlib.sha256(archive.read_bytes()).hexdigest() or any(
            evidence[key] != value for key, value in expected.items()): raise ValueError('storage_archive_invalid')
    volume = name + '-data'
    if run(['docker', 'volume', 'ls', '--filter', 'name=^' + volume + '$', '--format', '{{.Name}}']).strip():
        raise ValueError('restore_target_exists')
    run(['docker', 'volume', 'create', '--label', 'sandbox.recovery.owner=' + name, volume])
    run(['docker', 'create', '--name', name, '--label', 'sandbox.recovery.owner=' + name, '--network', 'none',
        '--user', '0', '--mount', 'type=volume,source=' + volume + ',target=/restore', '--entrypoint', 'sleep', source['image_id'], '3600'])
    run(['docker', 'start', name])
    run(['docker', 'exec', '-i', '--user', '0', name, 'tar', '-xf', '-', '-C', '/restore'], data=archive.read_bytes(), timeout=1800)
    actual = storage_tar(name, '/restore', work / 'restored.tar')
    if actual != expected: raise ValueError('storage_restore_failed')
    run(['docker', 'stop', '--time', '30', name])
    result = work / 'result.json'
    private_write(result, canonical({'schema_version': 1, 'ok': True, 'code': 'storage_restore_verified',
        'target': name, 'volume': volume, 'archive_digest': evidence['archive_digest'], 'observation': actual}))
    return result


def main():
    line = sys.stdin.buffer.readline(32769)
    if len(line) > 32768: raise ValueError('request_invalid')
    request = json.loads(line)
    resume = request.pop('resume_capture', False)
    if type(resume) is not bool: raise ValueError('request_invalid')
    if set(request) != {'operation', 'source', 'request_id', 'root', 'credential_size', 'credential_revision', 'archive_size', 'archive_digest', 'target_volume'}:
        raise ValueError('request_invalid')
    source = request['source']; operation = request['operation']; identity = request['request_id']
    if operation not in {'observe', 'capture', 'restore', 'status'} or not re.fullmatch(r'[a-f0-9]{64}', identity):
        raise ValueError('request_invalid')
    if resume and (operation != 'capture' or source['profile'] != 'lenzora-dev' or source['credential_reference'] is not None):
        raise ValueError('request_invalid')
    if type(request['credential_size']) is not int or not 0 <= request['credential_size'] <= 16384:
        raise ValueError('request_invalid')
    if type(request['archive_size']) is not int or not 0 <= request['archive_size'] <= MAX_ARCHIVE:
        raise ValueError('request_invalid')
    credential = sys.stdin.buffer.read(request['credential_size'])
    archive_bytes = sys.stdin.buffer.read(request['archive_size'])
    if len(credential) != request['credential_size'] or len(archive_bytes) != request['archive_size'] or sys.stdin.buffer.read(1):
        raise ValueError('request_invalid')
    if request['archive_digest'] != 'sha256:' + hashlib.sha256(archive_bytes).hexdigest():
        raise ValueError('archive_changed')
    root = Path(request['root'])
    if not root.is_absolute() or '..' in root.parts: raise ValueError('path_unsafe')
    if operation != 'status': root.mkdir(parents=True, mode=0o700, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError('path_unsafe')
    slot = root / identity
    if slot.is_symlink(): raise ValueError('path_unsafe')
    if resume and not slot.exists(): raise ValueError('acceptance_unknown')
    created = False
    if operation != 'status':
        try:
            slot.mkdir(mode=0o700); created = True
        except FileExistsError: pass
    descriptor = os.open(slot, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or info.st_mode & 0o077: raise ValueError('path_unsafe')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if created:
            if resume: raise ValueError('acceptance_unknown')
            private_write(slot / 'request.json', canonical(request))
        else:
            saved = json.loads(owned_read(slot / 'request.json', 32768))
            if operation == 'status':
                if saved.get('source') != source or saved.get('request_id') != identity:
                    raise ValueError('request_invalid')
                original = saved.get('operation')
                if original not in {'observe', 'capture', 'restore'}: raise ValueError('request_invalid')
                terminal = slot / ('capture.tar' if original == 'capture' else 'result.json')
                if terminal.is_symlink(): raise ValueError('path_unsafe')
                # Status never returns archive bytes or credentials.
                available = terminal.exists()
                if available: owned_read(terminal, MAX_ARCHIVE)
                result = {'ok': True, 'code': 'terminal_available' if available else 'retained_without_result',
                    'operation': original, 'source_digest': 'sha256:' + hashlib.sha256(canonical(source)).hexdigest()}
                sys.stdout.buffer.write(canonical(result)); return
            if saved != request: raise ValueError('acceptance_unknown')
            terminal = slot / ('capture.tar' if operation == 'capture' else 'result.json')
            if terminal.is_symlink(): raise ValueError('path_unsafe')
            if terminal.exists():
                sys.stdout.buffer.write(owned_read(terminal, MAX_ARCHIVE)); return
            if not resume: raise ValueError('acceptance_unknown')
        _execute(request, source, operation, identity, slot, credential, archive_bytes)
    finally:
        os.close(descriptor)


def _execute(request, source, operation, identity, slot, credential, archive_bytes):
    storage = source['profile'] == 'lenzora-prod-storage'
    if operation != 'restore':
        inspect_source(source)
    else:
        image = json.loads(run(['docker', 'image', 'inspect', source.get('client_image_id', source['image_id'])]))
        if len(image) != 1 or image[0]['Id'] != source.get('client_image_id', source['image_id']):
            raise ValueError('image_unavailable')
    client_name = 'sandbox-recovery-client-' + identity[:24]
    with tempfile.TemporaryDirectory(prefix='work-', dir=slot) as temporary:
        work = Path(temporary)
        client = None if storage else local_client(source)
        external = bool(source['credential_reference'])
        try:
            if external and operation != 'restore':
                client = external_client(source, credential, work, client_name)
            if storage:
                result_file = storage_operation(source, operation, work, archive_bytes, 'sandbox-recovery-restore-' + identity[:24])
                destination = slot / ('capture.tar' if operation == 'capture' else 'result.json')
                result_file.rename(destination)
                sys.stdout.buffer.write(destination.read_bytes())
            elif operation == 'observe':
                result = {'schema_version': 1, 'ok': True, 'code': 'observed', 'source': inspect_source(source),
                    'observation': observation(client, source['database'])}
                private_write(slot / 'result.json', canonical(result))
                sys.stdout.buffer.write(canonical(result))
            elif operation == 'capture':
                archive = capture(source, client, work)
                archive.rename(slot / 'capture.tar')
                sys.stdout.buffer.write((slot / 'capture.tar').read_bytes())
            else:
                private_write(work / 'input.tar', archive_bytes)
                result = restore(source, work / 'input.tar', work, 'sandbox-recovery-restore-' + identity[:24], request['target_volume'], credential)
                private_write(slot / 'result.json', canonical(result))
                sys.stdout.buffer.write(canonical(result))
        finally:
            if external and operation != 'restore':
                # Remove only this request's transient credential client after
                # checking its exact owner label. Source containers are untouched.
                try:
                    owner = json.loads(run(['docker','inspect',client_name]))[0]
                    if owner.get('Config',{}).get('Labels',{}).get('sandbox.recovery.owner') == client_name:
                        run(['docker','rm','-f',owner['Id']])
                except Exception: pass


if __name__ == '__main__':
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_ARCHIVE, MAX_ARCHIVE))
    try: main()
    except Exception:
        sys.stderr.write('postgres_recovery_failed\n'); sys.exit(1)
