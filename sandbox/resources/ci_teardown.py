"""Stack cleanup behind the CI broker's validated ownership boundary (066)."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import uuid

from sandbox.jobs.registry import read_resource_index
from .host_guard import try_reclaim_guard
from .workspace_teardown import teardown_workspace_stack


@contextmanager
def terminal_cleanup_guard(deployment_root):
    """Hold reclaim exclusion through identity checks, stack and broker removal."""
    if deployment_root is None:
        yield
        return
    runtime = Path(deployment_root).parent / 'runtime'
    with try_reclaim_guard(runtime=runtime) as acquired:
        if not acquired:
            raise RuntimeError('host_reclaim_busy')
        yield


def _record(path, kind, locator):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        data = (json.dumps({'phase':'intent','kind':kind,'path':locator,
                           'at':datetime.now(timezone.utc).isoformat()}) + '\n').encode()
        written = os.write(descriptor, data)
        if written != len(data):
            raise OSError('short deletion record')
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _run(argv, timeout):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                start_new_session=True, check=False)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 124, '', 'timeout'
    except OSError:
        return 1, '', 'engine_unavailable'


def complete_terminal_stack(checkout, deployment_root, job_repository):
    """Called only after the caller proves terminal CI source identity/policy.

    The caller holds its submission guard and terminal_cleanup_guard throughout.
    Non-workspace checkouts retain the broker's existing absence requirement.
    """
    checkout = Path(checkout)
    if checkout.name.startswith('sandbox-host-'):
        raise RuntimeError('hosted_site')
    if '-workspace-' not in checkout.name:
        return
    if checkout.parent != Path(deployment_root):
        raise RuntimeError('workspace_path_escape')
    index = read_resource_index(job_repository.path)
    jobs = index.get('jobs')
    if not isinstance(jobs, list) or len(jobs) >= 10000:
        raise RuntimeError('job_state_unknown')
    terminal = {'succeeded','failed','timed_out','cancelled','interrupted'}
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get('project_root'), str):
            raise RuntimeError('job_state_unknown')
        if Path(job['project_root']).resolve() == checkout.resolve() and job.get('lifecycle') not in terminal:
            raise RuntimeError('active_job_protected')
    manifest = Path(deployment_root).parent / 'runtime' / 'resources' / 'deletions' / ('ci-' + uuid.uuid4().hex + '.jsonl')
    result = teardown_workspace_stack(_run, checkout.name, [],
                                     record=lambda kind, locator: _record(manifest, kind, locator))
    if not result.get('ok'):
        raise RuntimeError('stack_teardown_incomplete')


def cleanup_completed(result):
    """A retained/refused workspace must not become completed after log removal."""
    return isinstance(result, dict) and result.get('ok') is True and result.get('status') in {
        'released', 'already_released', 'completed', 'removed',
    }
