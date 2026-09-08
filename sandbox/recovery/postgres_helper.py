"""Installed, bounded PostgreSQL capture and disposable restore helper.

Only the registered recovery transport invokes this helper. stdin carries a
closed request and optional broker material; stdout is fixed metadata or one
private capture archive. No source rows, connection strings or errors are logged.
"""
from __future__ import annotations

import hashlib
import fcntl
import io
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
MAX_SCHEMA_BYTES = 8 * 1024 * 1024
_TEMP_COUNTS_SQL = 'CREATE TEMP TABLE recovery_counts (name text, count bigint);\n'
_SCHEMA_FIELDS_SQL = r'''
 'constraints', (SELECT coalesce(json_agg(json_build_object('table',c.relname,'name',con.conname,'definition',pg_get_constraintdef(con.oid),'validated',con.convalidated) ORDER BY c.relname,con.conname),'[]') FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
 'columns', (SELECT coalesce(json_agg(json_build_object('table',table_name,'column',column_name,'type',data_type,'nullable',is_nullable) ORDER BY table_name,ordinal_position),'[]') FROM information_schema.columns WHERE table_schema='public')
'''


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


def _schema_projection(raw):
    if type(raw) is not dict:
        raise ValueError('schema_metadata_invalid')
    projected = {}
    for kind, identity, fields in (
            ('constraints', 'name', {'table', 'name', 'definition', 'validated'}),
            ('columns', 'column', {'table', 'column', 'type', 'nullable'})):
        rows = raw.get(kind)
        if type(rows) is not list or len(rows) > 10000:
            raise ValueError('schema_metadata_invalid')
        seen = set()
        for row in rows:
            if type(row) is not dict or set(row) != fields:
                raise ValueError('schema_metadata_invalid')
            for field in fields:
                value = row[field]
                if field == 'validated':
                    if type(value) is not bool: raise ValueError('schema_metadata_invalid')
                elif (type(value) is not str or not value
                        or len(value.encode()) > (65536 if field == 'definition' else 128)):
                    raise ValueError('schema_metadata_invalid')
            if kind == 'columns' and row['nullable'] not in {'YES', 'NO'}:
                raise ValueError('schema_metadata_invalid')
            key = (row['table'], row[identity])
            if key in seen: raise ValueError('schema_metadata_invalid')
            seen.add(key)
        projected[kind] = rows
    if len(canonical(projected)) > MAX_SCHEMA_BYTES:
        raise ValueError('schema_metadata_invalid')
    return projected


def _schema_digest(value):
    return 'sha256:' + hashlib.sha256(canonical(value)).hexdigest()


def _definition_shape(value):
    """Bounded syntax labels only; never emit identifiers or literal values."""
    keywords = {'CHECK', 'AND', 'OR', 'NOT', 'IS', 'NULL', 'TRUE', 'FALSE',
        'ANY', 'ALL', 'ARRAY', 'BETWEEN', 'IN', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END'}
    tokens = re.findall(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9$]*|[0-9]+|[^\s]", value)
    shape = []
    for token in tokens[:512]:
        if token.startswith("'"):
            label = 'string'
        elif token.startswith('"'):
            label = 'identifier'
        elif token.upper() in keywords:
            label = token.upper()
        elif token.isdigit():
            label = 'number'
        elif len(token) == 1 and token in '(),[]:+-*/%<>=!~|&.^':
            label = token
        else:
            label = 'identifier'
        shape.append(label)
    return {'syntax_labels': shape, 'truncated': len(tokens) > 512}


def schema_records(client, database):
    # Match observation's session context, including its temporary namespace.
    # Only session-local DDL precedes the read-only metadata query.
    payload = sql(client, database, _TEMP_COUNTS_SQL
        + 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSELECT json_build_object('
        + _SCHEMA_FIELDS_SQL + ');')
    if len(payload.encode()) > MAX_SCHEMA_BYTES:
        raise ValueError('schema_metadata_invalid')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('schema_metadata_invalid')
            result[key] = value
        return result
    try:
        return _schema_projection(json.loads(payload, object_pairs_hook=pairs))
    except (ValueError, RecursionError):
        raise ValueError('schema_metadata_invalid') from None


def schema_diagnostic(source, target_client, captured_digest, observed_target_digest):
    """Explain a mismatch without changing any restore acceptance decision."""
    inspect_source(source)
    original = schema_records(local_client(source), source['database'])
    restored = schema_records(target_client, source['database'])
    inspect_source(source)
    source_digest, target_digest = _schema_digest(original), _schema_digest(restored)
    result = {'schema_version': 1, 'captured_digest': captured_digest,
        'source_digest': source_digest, 'target_digest': target_digest,
        'source_matches_capture': source_digest == captured_digest}
    if not result['source_matches_capture']:
        return {**result, 'code': 'source_schema_changed'}
    if target_digest != observed_target_digest:
        return {**result, 'code': 'target_schema_changed'}
    differences = []; components = {}; record_sets_equal = True
    for kind, identity in (('constraints', 'name'), ('columns', 'column')):
        left = {(row['table'], row[identity]): row for row in original[kind]}
        right = {(row['table'], row[identity]): row for row in restored[kind]}
        equal = left == right
        record_sets_equal = record_sets_equal and equal
        components[kind] = {'source_count': len(left), 'target_count': len(right),
            'source_digest': _schema_digest(original[kind]), 'target_digest': _schema_digest(restored[kind]),
            'records_equal': equal, 'record_order_equal': original[kind] == restored[kind]}
        for key in sorted(left.keys() | right.keys()):
            if key not in right:
                change, fields = 'missing', []
            elif key not in left:
                change, fields = 'extra', []
            else:
                fields = sorted(field for field in left[key] if left[key][field] != right[key][field])
                if not fields: continue
                change = 'changed'
            difference = {'kind': kind, 'table': key[0], 'name': key[1],
                          'change': change, 'fields': fields}
            if change == 'changed' and kind == 'constraints' and 'definition' in fields:
                difference['source_shape'] = _definition_shape(left[key]['definition'])
                difference['target_shape'] = _definition_shape(right[key]['definition'])
            differences.append(difference)
    def column_sequences(schema):
        sequences = {}
        for row in schema['columns']:
            sequences.setdefault(row['table'], []).append(row['column'])
        return sequences
    left_order, right_order = column_sequences(original), column_sequences(restored)
    for table in sorted(left_order.keys() & right_order.keys()):
        if set(left_order[table]) == set(right_order[table]) and left_order[table] != right_order[table]:
            differences.append({'kind': 'columns', 'table': table, 'name': '',
                                'change': 'changed', 'fields': ['order']})
    column_order_equal = left_order == right_order
    return {**result, 'code': 'schema_compared', 'components': components,
        'record_sets_equal': record_sets_equal, 'column_order_equal': column_order_equal,
        'ordering_only': record_sets_equal and column_order_equal and source_digest != target_digest,
        'difference_count': len(differences), 'differences': differences[:32],
        'truncated': len(differences) > 32}


def observation(client, database, prefix=""):
    # Identifiers come only from pg_catalog and are quoted by format('%I').
    # CREATE is forbidden inside READ ONLY, even for temporary tables. Create
    # the session-local accumulator before importing the read-only snapshot.
    query = _TEMP_COUNTS_SQL + prefix + r'''
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
''' + _SCHEMA_FIELDS_SQL + ');'
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
        "schema_digest": _schema_digest(_schema_projection(raw)),
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


def restore_input(archive, work):
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
    return evidence, dump


def restore_target(source, name):
    volume = name + '-data'
    rows = json.loads(run(['docker', 'inspect', name]))
    if len(rows) != 1: raise ValueError('restore_target_changed')
    row = rows[0]
    state = row.get('State') or {}; config = row.get('HostConfig') or {}
    if (row.get('Name') != '/' + name or row.get('Image') != source['client_image_id']
            or row.get('Config', {}).get('Labels', {}).get('sandbox.recovery.owner') != name
            or type(state.get('Running')) is not bool or state.get('Paused') or state.get('Restarting')
            or config.get('NetworkMode') != 'none' or config.get('PortBindings')
            or not any(m.get('Type') == 'volume' and m.get('Name') == volume
                and m.get('Destination') == '/var/lib/postgresql/data' for m in row.get('Mounts', []))):
        raise ValueError('restore_target_changed')
    volumes = json.loads(run(['docker', 'volume', 'inspect', volume]))
    if len(volumes) != 1 or volumes[0].get('Labels', {}).get('sandbox.recovery.owner') != name:
        raise ValueError('restore_target_changed')
    consumers = run(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'volume=' + volume]).decode().split()
    if consumers != [row.get('Id')]: raise ValueError('restore_target_changed')
    if not re.fullmatch(r'[a-f0-9]{64}', row.get('Id', '')): raise ValueError('restore_target_changed')
    return row, volumes[0]


def inspect_restore(source, archive, work, name, *, slot=None):
    evidence, _dump = restore_input(archive, work)
    row, _volume = restore_target(source, name)
    volume = name + '-data'
    base = {'schema_version': 1, 'ok': True, 'code': 'restore_inspected', 'target': name,
            'container_id': row['Id'], 'volume': volume}
    if not row['State']['Running']:
        if slot is None: raise ValueError('restore_target_stopped')
        plan = prepare_reopen(source, archive, evidence, name, slot)
        return {**base, 'code': 'restore_target_stopped', 'database_available': False,
                'all_match': False, 'reopen_plan': plan}
    client = ['docker', 'exec', '-i', '--user', 'postgres', '-e', 'PGUSER=' + source['role'], row['Id']]
    try:
        importers = sql(client, source['database'], "SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND application_name='pg_restore';")
    except ValueError:
        return {**base, 'database_available': False, 'all_match': False, 'matches': {}}
    if importers != '0':
        raise ValueError('restore_target_busy')
    actual = observation(client, source['database'])
    matches = {key: actual[key] == evidence.get(key) for key in actual}
    expected_counts = {row['name']: row['count'] for row in evidence['table_counts']}
    actual_counts = {row['name']: row['count'] for row in actual['table_counts']}
    different = sorted(name for name in expected_counts.keys() | actual_counts.keys()
        if expected_counts.get(name) != actual_counts.get(name))
    diagnostic = {}
    if not matches['schema_digest']:
        try:
            diagnostic['schema_diagnostic'] = schema_diagnostic(source, client,
                evidence['schema_digest'], actual['schema_digest'])
        except (ValueError, OSError, KeyError, subprocess.TimeoutExpired):
            diagnostic['schema_diagnostic'] = {'schema_version': 1, 'code': 'schema_diagnostic_unavailable'}
    return {**base, 'database_available': True, 'matches': matches, 'all_match': all(matches.values()),
        'observation': actual, 'dump_digest': evidence['dump_digest'],
        'source_database_identity': evidence['database_identity'],
        'target_database': source['database'], 'target_role': source['role'],
        'source_table_count': len(expected_counts), 'restored_table_count': len(actual_counts),
        'mismatched_table_count': len(different), 'mismatched_tables': different[:32], **diagnostic}


_REOPEN_FIELDS = {'schema_version', 'native_request_id', 'source_digest', 'archive_digest',
    'dump_digest', 'target', 'container_id', 'daemon_identity', 'image_id', 'volume',
    'configuration_digest', 'state_digest', 'data_marker_digest', 'generation',
    'previous_plan_digest', 'plan_digest'}


def validate_reopen_plan(plan):
    if (type(plan) is not dict or set(plan) != _REOPEN_FIELDS
            or type(plan['schema_version']) is not int or plan['schema_version'] != 1
            or type(plan['generation']) is not int or not 0 <= plan['generation'] < 16):
        raise ValueError('reopen_plan_changed')
    for field in ('native_request_id', 'container_id'):
        if type(plan[field]) is not str or not re.fullmatch(r'[a-f0-9]{64}', plan[field]):
            raise ValueError('reopen_plan_changed')
    for field in ('source_digest', 'archive_digest', 'dump_digest', 'image_id',
                  'configuration_digest', 'state_digest', 'data_marker_digest', 'plan_digest'):
        if type(plan[field]) is not str or not re.fullmatch(r'sha256:[a-f0-9]{64}', plan[field]):
            raise ValueError('reopen_plan_changed')
    previous = plan['previous_plan_digest']
    if previous is not None and (type(previous) is not str or not re.fullmatch(r'sha256:[a-f0-9]{64}', previous)):
        raise ValueError('reopen_plan_changed')
    if ((plan['generation'] == 0) != (previous is None)
            or plan['target'] != 'sandbox-recovery-restore-' + plan['native_request_id'][:24]
            or plan['volume'] != plan['target'] + '-data'
            or type(plan['daemon_identity']) is not str
            or not re.fullmatch(r'[a-zA-Z0-9:_.-]{1,128}', plan['daemon_identity'])
            or plan['plan_digest'] != _schema_digest({k: v for k, v in plan.items() if k != 'plan_digest'})):
        raise ValueError('reopen_plan_changed')
    return plan


def _owned_reopen_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('path_unsafe')


def _durable_reopen_record(path, value):
    private_write(path, canonical(value))
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def _durable_reopen_directory(path):
    path.mkdir(mode=0o700)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def _closed_pairs(items):
    result = {}
    for key, value in items:
        if key in result: raise ValueError('request_invalid')
        result[key] = value
    return result


def reopen_history(slot):
    root = slot / 'reopens'
    if not root.exists() and not root.is_symlink(): return []
    _owned_reopen_directory(root)
    names = sorted(item.name for item in root.iterdir())
    if len(names) > 16 or names != [f'{index:04d}' for index in range(len(names))]:
        raise ValueError('reopen_history_invalid')
    history = []; previous = None
    for index, name in enumerate(names):
        directory = root / name; _owned_reopen_directory(directory)
        entries = {item.name for item in directory.iterdir()}
        if entries not in ({'intent.json'}, {'intent.json', 'result.json'}):
            raise ValueError('reopen_history_invalid')
        plan = validate_reopen_plan(json.loads(owned_read(directory / 'intent.json', 16384), object_pairs_hook=_closed_pairs))
        if (plan['native_request_id'] != slot.name or plan['generation'] != index
                or plan['previous_plan_digest'] != previous):
            raise ValueError('reopen_history_invalid')
        result = None
        if 'result.json' in entries:
            result = json.loads(owned_read(directory / 'result.json', 16384), object_pairs_hook=_closed_pairs)
            expected = _reopened_result(plan)
            if result != expected: raise ValueError('reopen_history_invalid')
        elif index != len(names) - 1:
            raise ValueError('reopen_history_invalid')
        history.append((plan, result)); previous = plan['plan_digest']
    return history


def _data_marker(container_id, name, maximum):
    payload = run(['docker', 'cp', container_id + ':/var/lib/postgresql/data/' + name, '-'])
    if len(payload) > 65536: raise ValueError('restore_data_invalid')
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as archive:
            entries = archive.getmembers()
            if (len(entries) != 1 or not entries[0].isfile()
                    or Path(entries[0].name).name != Path(name).name
                    or not 0 < entries[0].size <= maximum):
                raise ValueError('restore_data_invalid')
            return archive.extractfile(entries[0]).read()
    except (tarfile.TarError, OSError):
        raise ValueError('restore_data_invalid') from None


def reopen_snapshot(source, name, major, *, stopped):
    row, volume = restore_target(source, name)
    config = row['Config']; host = row['HostConfig']; state = row['State']
    if (source.get('profile') != 'lenzora-dev' or source.get('credential_reference') is not None
            or config.get('Entrypoint') != ['sleep'] or config.get('Cmd') != ['3600']
            or config.get('User') not in ('', '0', 'root')
            or host.get('RestartPolicy', {}).get('Name') not in ('', 'no')
            or host.get('Privileged') is not False or host.get('PublishAllPorts') is not False
            or any(host.get(key) for key in ('Binds', 'VolumesFrom', 'Devices', 'DeviceRequests', 'CapAdd', 'SecurityOpt'))
            or host.get('PidMode') not in ('', None) or host.get('UTSMode') not in ('', None)
            or host.get('UsernsMode') not in ('', None) or host.get('IpcMode') not in ('private', '')
            or host.get('Tmpfs') != {'/run/recovery': 'rw,noexec,nosuid,mode=0700'}
            or volume.get('Name') != name + '-data' or volume.get('Driver') != 'local'
            or volume.get('Options') or volume.get('Scope') != 'local'):
        raise ValueError('restore_target_changed')
    mounts = row.get('Mounts', [])
    for mount in mounts:
        if (mount.get('Type') == 'volume' and mount.get('Name') == name + '-data'
                and mount.get('Destination') == '/var/lib/postgresql/data' and mount.get('RW') is True):
            continue
        if mount.get('Type') == 'tmpfs' and mount.get('Destination') == '/run/recovery': continue
        raise ValueError('restore_target_changed')
    for key in ('Running', 'Paused', 'Restarting', 'Dead', 'OOMKilled'):
        if type(state.get(key)) is not bool: raise ValueError('restore_target_changed')
    if (state['Paused'] or state['Restarting'] or state['Dead'] or state['OOMKilled']
            or type(state.get('Pid')) is not int or type(state.get('ExitCode')) is not int
            or any(type(state.get(key)) is not str or len(state[key]) > 64 for key in ('StartedAt', 'FinishedAt'))
            or (stopped and (state['Running'] or state['Pid'] != 0 or state.get('Status') != 'exited' or state['ExitCode'] not in (0, 137)))
            or (not stopped and (not state['Running'] or state['Pid'] <= 0 or state.get('Status') != 'running'))):
        raise ValueError('restore_target_changed')
    images = json.loads(run(['docker', 'image', 'inspect', source['client_image_id']]))
    if len(images) != 1 or images[0].get('Id') != source['client_image_id']:
        raise ValueError('restore_target_changed')
    def environment(values):
        if type(values) is not list or len(values) > 128: raise ValueError('restore_target_changed')
        result = {}
        for value in values:
            if type(value) is not str or '=' not in value or len(value) > 16384:
                raise ValueError('restore_target_changed')
            key, item = value.split('=', 1)
            if key in result: raise ValueError('restore_target_changed')
            result[key] = item
        return result
    expected_env = environment(images[0].get('Config', {}).get('Env', []))
    expected_env.update(POSTGRES_HOST_AUTH_METHOD='scram-sha-256', POSTGRES_USER=source['role'], POSTGRES_DB=source['database'])
    if environment(config.get('Env')) != expected_env: raise ValueError('restore_target_changed')
    daemon = run(['docker', 'info', '--format', '{{.ID}}']).decode().strip()
    if not re.fullmatch(r'[a-zA-Z0-9:_.-]{1,128}', daemon): raise ValueError('restore_target_changed')
    configuration = {'image': row['Image'], 'config': config, 'host': host, 'mounts': mounts,
                     'volume': volume}
    result = {'container_id': row['Id'], 'daemon_identity': daemon,
              'configuration_digest': _schema_digest(configuration)}
    if stopped:
        version = _data_marker(row['Id'], 'PG_VERSION', 32)
        control = _data_marker(row['Id'], 'global/pg_control', 16384)
        if version != (str(major) + '\n').encode(): raise ValueError('restore_data_invalid')
        result.update(state_digest=_schema_digest({key: state[key] for key in (
            'Status', 'Running', 'Paused', 'Restarting', 'Dead', 'OOMKilled', 'Pid', 'ExitCode', 'StartedAt', 'FinishedAt')}),
            data_marker_digest='sha256:' + hashlib.sha256(version + b'\0' + control).hexdigest())
    return result


def prepare_reopen(source, archive, evidence, name, slot):
    history = reopen_history(slot)
    if history and history[-1][1] is None: raise ValueError('reopen_pending')
    if len(history) >= 16: raise ValueError('reopen_history_invalid')
    snapshot = reopen_snapshot(source, name, evidence['major'], stopped=True)
    body = {'schema_version': 1, 'native_request_id': slot.name,
        'source_digest': _schema_digest(source), 'archive_digest': 'sha256:' + hashlib.sha256(archive.read_bytes()).hexdigest(),
        'dump_digest': evidence['dump_digest'], 'target': name, 'image_id': source['client_image_id'],
        'volume': name + '-data', 'generation': len(history),
        'previous_plan_digest': history[-1][0]['plan_digest'] if history else None, **snapshot}
    return validate_reopen_plan({**body, 'plan_digest': _schema_digest(body)})


def _reopened_result(plan):
    return {'schema_version': 1, 'ok': True, 'code': 'restore_reopened',
        'target': plan['target'], 'container_id': plan['container_id'], 'volume': plan['volume'],
        'reopen_generation': plan['generation'], 'reopen_plan_digest': plan['plan_digest'],
        'database_available': True}


def _observe_reopened(source, evidence, plan):
    observed = reopen_snapshot(source, plan['target'], evidence['major'], stopped=False)
    if any(observed[key] != plan[key] for key in ('container_id', 'daemon_identity', 'configuration_digest')):
        raise ValueError('restore_target_changed')
    client = ['docker', 'exec', '-i', '--user', 'postgres', '-e', 'PGUSER=' + source['role'], plan['container_id']]
    value = run([*client, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '--dbname', source['database']],
        data=b"SELECT current_setting('server_version_num')::int/10000;", timeout=10).decode().strip()
    if value != str(evidence['major']): raise ValueError('restore_database_unavailable')


def reopen_restore(source, archive, work, name, slot, plan):
    plan = validate_reopen_plan(plan)
    evidence, _dump = restore_input(archive, work)
    if (source.get('profile') != 'lenzora-dev' or source.get('credential_reference') is not None
            or plan['native_request_id'] != slot.name or plan['target'] != name
            or plan['source_digest'] != _schema_digest(source) or plan['image_id'] != source['client_image_id']
            or plan['archive_digest'] != 'sha256:' + hashlib.sha256(archive.read_bytes()).hexdigest()
            or plan['dump_digest'] != evidence['dump_digest']):
        raise ValueError('reopen_plan_changed')
    history = reopen_history(slot)
    generation = plan['generation']; directory = slot / 'reopens' / f'{generation:04d}'
    if generation < len(history):
        if history[generation][0] != plan or generation != len(history) - 1:
            raise ValueError('reopen_plan_changed')
        # A retry can recover positive completion, but never repeat an uncertain
        # start. No subprocess mutation occurs along this branch.
        try: _observe_reopened(source, evidence, plan)
        except (ValueError, OSError, subprocess.TimeoutExpired): raise ValueError('reopen_pending') from None
        result = _reopened_result(plan)
        if history[generation][1] is None: _durable_reopen_record(directory / 'result.json', result)
        return result
    if plan != prepare_reopen(source, archive, evidence, name, slot): raise ValueError('reopen_plan_changed')
    root = slot / 'reopens'
    if not root.exists(): _durable_reopen_directory(root)
    _owned_reopen_directory(root)
    _durable_reopen_directory(directory)
    _durable_reopen_record(directory / 'intent.json', plan)
    expected = reopen_snapshot(source, name, evidence['major'], stopped=True)
    if any(expected[key] != plan[key] for key in expected): raise ValueError('reopen_plan_changed')
    try:
        run(['docker', 'start', plan['container_id']])
        current = reopen_snapshot(source, name, evidence['major'], stopped=False)
        if any(current[key] != plan[key] for key in current): raise ValueError('restore_target_changed')
        run(['docker', 'exec', '-d', '--user', 'postgres', plan['container_id'],
             'postgres', '-D', '/var/lib/postgresql/data'])
        deadline = time.monotonic() + 90
        while True:
            try:
                _observe_reopened(source, evidence, plan)
                break
            except (ValueError, OSError, subprocess.TimeoutExpired):
                if time.monotonic() >= deadline: raise ValueError('reopen_pending') from None
                time.sleep(1)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        raise ValueError('reopen_pending') from None
    result = _reopened_result(plan)
    _durable_reopen_record(directory / 'result.json', result)
    return result


def restore(source, archive, work, name, target_volume=None, target_password=b''):
    evidence, dump = restore_input(archive, work)
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
    request = json.loads(line, object_pairs_hook=_closed_pairs)
    resume = request.pop('resume_capture', False)
    reopen_plan = request.pop('reopen_plan', None)
    if type(resume) is not bool: raise ValueError('request_invalid')
    if set(request) != {'operation', 'source', 'request_id', 'root', 'credential_size', 'credential_revision', 'archive_size', 'archive_digest', 'target_volume'}:
        raise ValueError('request_invalid')
    source = request['source']; operation = request['operation']; identity = request['request_id']
    if operation not in {'observe', 'capture', 'restore', 'inspect-restore', 'verify-restore', 'reopen-restore', 'status'} or not re.fullmatch(r'[a-f0-9]{64}', identity):
        raise ValueError('request_invalid')
    if operation in {'inspect-restore', 'verify-restore', 'reopen-restore'} and (source['profile'] != 'lenzora-dev' or request['target_volume'] is not None):
        raise ValueError('request_invalid')
    if operation == 'reopen-restore':
        validate_reopen_plan(reopen_plan)
        if source.get('credential_reference') is not None: raise ValueError('request_invalid')
    elif reopen_plan is not None:
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
    if operation not in {'status', 'inspect-restore', 'verify-restore', 'reopen-restore'}: root.mkdir(parents=True, mode=0o700, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError('path_unsafe')
    slot = root / identity
    if slot.is_symlink(): raise ValueError('path_unsafe')
    if resume and not slot.exists(): raise ValueError('acceptance_unknown')
    created = False
    if operation not in {'status', 'inspect-restore', 'verify-restore', 'reopen-restore'}:
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
            expected = {**request, 'operation': 'restore'} if operation in {'inspect-restore', 'verify-restore', 'reopen-restore'} else request
            if saved != expected: raise ValueError('acceptance_unknown')
            terminal = slot / ('capture.tar' if operation == 'capture' else 'result.json')
            if terminal.is_symlink(): raise ValueError('path_unsafe')
            if terminal.exists():
                if operation == 'reopen-restore': raise ValueError('request_invalid')
                sys.stdout.buffer.write(owned_read(terminal, MAX_ARCHIVE)); return
            if not resume and operation not in {'inspect-restore', 'verify-restore', 'reopen-restore'}: raise ValueError('acceptance_unknown')
        _execute(request, source, operation, identity, slot, credential, archive_bytes, reopen_plan=reopen_plan)
    finally:
        os.close(descriptor)


def _execute(request, source, operation, identity, slot, credential, archive_bytes, *, reopen_plan=None):
    if operation in {'inspect-restore', 'verify-restore', 'reopen-restore'}:
        with tempfile.TemporaryDirectory(prefix='inspect-', dir=slot) as temporary:
            work = Path(temporary); archive = work / 'input.tar'
            private_write(archive, archive_bytes)
            name = 'sandbox-recovery-restore-' + identity[:24]
            if operation == 'reopen-restore':
                result = reopen_restore(source, archive, work, name, slot, reopen_plan)
            else:
                result = inspect_restore(source, archive, work, name, slot=slot)
            if operation == 'verify-restore':
                if result.get('all_match') is not True: raise ValueError('restore_verification_failed')
                current, _volume = restore_target(source, name)
                if current['Id'] != result['container_id'] or not current['State']['Running']:
                    raise ValueError('restore_target_changed')
                run(['docker', 'exec', '--user', 'postgres', current['Id'], 'pg_ctl', '-D', '/var/lib/postgresql/data', '-m', 'fast', '-w', 'stop'])
                run(['docker', 'stop', '--time', '30', current['Id']])
                result = {key: result[key] for key in ('schema_version', 'ok', 'target', 'volume',
                    'target_database', 'target_role', 'source_database_identity', 'dump_digest', 'observation')}
                result['code'] = 'restore_verified'
                private_write(slot / 'result.json', canonical(result))
            sys.stdout.buffer.write(canonical(result))
        return
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


def safe_main():
    try: main()
    except Exception as error:
        code = str(error) if type(error) is ValueError else ''
        if code not in {'restore_target_changed', 'restore_target_stopped', 'restore_target_busy',
                'restore_data_invalid', 'reopen_plan_changed', 'reopen_pending', 'reopen_history_invalid',
                'restore_database_unavailable', 'restore_verification_failed', 'request_invalid',
                'archive_changed', 'source_changed', 'path_unsafe'}:
            sys.stderr.write('postgres_recovery_failed\n')
            return 1
        sys.stdout.buffer.write(canonical({'schema_version': 1, 'ok': False, 'code': code}))
    return 0


if __name__ == '__main__':
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_ARCHIVE, MAX_ARCHIVE))
    sys.exit(safe_main())
