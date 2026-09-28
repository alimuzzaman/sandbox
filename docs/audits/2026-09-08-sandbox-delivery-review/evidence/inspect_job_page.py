"""Read-only diagnostic wrapper; prints only remote page size/shape metadata.

Runs the supported job-list CLI without changing the parser's result. Captured
job payloads and diagnostics stay in memory and are never saved or printed.
"""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

if len(sys.argv) != 2:
    raise SystemExit('usage: inspect_job_page.py REGISTERED_REMOTE')
remote_name = sys.argv[1]
sys.argv = ['sb', 'job-list', '--remote', remote_name, '--limit', '100', '--json']
from sandbox.transports import remote_jobs
from sandbox.cli import main

original = remote_jobs._last_json
observations = []

def inspect_page(value):
    data = {'bytes': len(value.encode('utf-8', errors='replace')),
            'limit_bytes': remote_jobs._MAX_REMOTE_JSON_BYTES,
            'lines': len(value.splitlines()), 'valid_json': False}
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        pass
    else:
        data['valid_json'] = True
        if isinstance(parsed, dict):
            data['ok'] = parsed.get('ok') if isinstance(parsed.get('ok'), bool) else None
            for key in ('jobs', 'items'):
                if isinstance(parsed.get(key), list):
                    data['record_count'] = len(parsed[key])
    result = original(value)
    data['accepted_by_parser'] = result is not None
    observations.append(data)
    return result

stdout, stderr = io.StringIO(), io.StringIO()
exit_code = 0
with redirect_stdout(stdout), redirect_stderr(stderr), \
     patch.object(remote_jobs, '_last_json', side_effect=inspect_page):
    try:
        main()
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1

print(json.dumps({'observed_at': datetime.now(timezone.utc).isoformat(),
                  'operation': 'job-list', 'remote': remote_name, 'limit': 100,
                  'cli_exit_code': exit_code, 'pages': observations}, indent=2))
