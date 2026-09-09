"""Read-only ordinary delivery admission evidence; never grants effect authority."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
import time

from sandbox.jobs.registry import read_delivery_job_evidence
from sandbox.jobs.process import capture_process_identity

_CONTEXT_KEYS = {name: 'SANDBOX_DURABLE_' + name.upper() for name in (
    'job_id', 'request_id', 'project_identity', 'project_root_digest',
    'source_identity', 'source_commit', 'source_dirty_digest')}


class AdmissionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def validate_durable_context(project_dir: str | Path, *, database_path=None,
                             project_identity: str | None = None,
                             wait_seconds: float = 5) -> dict:
    """Bind fixed child context to retained, original and current source evidence.

    The caller re-runs this under its existing mutation/registration fences.
    This function never submits work or modifies the job registry.
    """
    fields = {key: os.environ.get(value) for key, value in _CONTEXT_KEYS.items()}
    required = set(fields) - {'source_dirty_digest'}
    if any(not fields[key] for key in required):
        raise AdmissionError('recovery_context_required')
    if any(not isinstance(fields[key], str) or len(fields[key].encode()) > 128
           or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]*', fields[key]) is None
           for key in required):
        raise AdmissionError('recovery_context_invalid')
    if fields['source_dirty_digest']:
        raise AdmissionError('recovery_source_dirty')
    root = Path(project_dir).expanduser().resolve()
    root_digest = 'sha256:' + hashlib.sha256(str(root).encode()).hexdigest()
    if fields['project_root_digest'] != root_digest or fields['source_identity'] != root_digest:
        raise AdmissionError('recovery_source_mismatch')
    if project_identity is None:
        from sandbox.config.facade import project_identity as canonical_project_identity
        project_identity = canonical_project_identity({'root': root})['identity']
    if fields['project_identity'] != project_identity:
        raise AdmissionError('recovery_source_mismatch')
    if database_path is None:
        from sandbox.core._paths import RUNTIME_DIR
        database_path = RUNTIME_DIR / 'jobs' / 'registry.sqlite3'
    deadline = time.monotonic() + min(5.0, max(0.0, float(wait_seconds)))
    while True:
        evidence = read_delivery_job_evidence(database_path, fields['job_id'])
        job, submitted = evidence['job'], evidence['submitted']
        if not job or not submitted or submitted.get('version') != 1:
            raise AdmissionError('recovery_context_invalid')
        if (job.get('lifecycle') != 'running' or job.get('finished_at') is not None
                or not job.get('started_at') or job.get('target_kind') != 'local'
                or job.get('remote_name') is not None):
            raise AdmissionError('recovery_context_invalid')
        for key in ('request_id', 'project_identity', 'source_identity', 'source_commit'):
            if job.get(key) != fields[key] or submitted.get(key) != fields[key]:
                raise AdmissionError('recovery_source_mismatch')
        if (job.get('project_root') != str(root) or submitted.get('project_root') != str(root)
                or submitted.get('target_kind') != 'local' or submitted.get('remote_name') is not None):
            raise AdmissionError('recovery_source_mismatch')
        if job.get('source_dirty_digest') is not None or submitted.get('source_dirty_digest') is not None:
            raise AdmissionError('recovery_source_dirty')
        process = evidence['process']
        missing_child = not process or any(process.get(k) is None for k in (
            'child_pid', 'child_pgid', 'child_start_identity'))
        if not missing_child:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AdmissionError('recovery_context_invalid')
        time.sleep(min(0.05, remaining))
    pid, pgid = process['child_pid'], process['child_pgid']
    if type(pid) is not int or type(pgid) is not int or pid <= 0 or pgid <= 0:
        raise AdmissionError('recovery_context_invalid')
    observed = capture_process_identity(pid)
    try:
        group_matches = os.getpgid(pid) == pgid == os.getpgrp()
    except OSError:
        group_matches = False
    if (not observed or not group_matches
            or re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', observed.host_boot_id) is None
            or observed.host_boot_id != process['host_boot_id']
            or observed.start_identity != process['child_start_identity']):
        raise AdmissionError('recovery_context_invalid')
    # Disable optional Git locks and ignore inherited Git override variables.
    environment = {'PATH': os.defpath, 'GIT_OPTIONAL_LOCKS': '0', 'LC_ALL': 'C'}
    try:
        commit = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD^{commit}'],
            capture_output=True, text=True, timeout=5, check=True, env=environment).stdout.strip()
        dirty = subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=normal'],
            capture_output=True, text=True, timeout=5, check=True, env=environment).stdout
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise AdmissionError('recovery_source_mismatch') from None
    if dirty:
        raise AdmissionError('recovery_source_dirty')
    if re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', commit) is None or commit != fields['source_commit']:
        raise AdmissionError('recovery_source_mismatch')
    return fields
