"""Original cancellation and deadline authority across detached generations."""
from datetime import datetime, timedelta, timezone
import json

CANCEL_EVENT = 'server_cli_cancel_requested'
MAX_ANCESTORS = 128


class ServerCliLineageError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError()
    return parsed


def check_server_cli_lineage(rows, *, pending_self=False):
    """Return the original minimum deadline; succeeded ancestors remain valid.

    Rows come only from the repository's actual parent links and durable event
    ledger. This observation does not establish actor or process authority.
    """
    if not isinstance(rows, (list, tuple)) or not rows or len(rows) > MAX_ANCESTORS:
        raise ServerCliLineageError('parent_context_unavailable')
    deadline = None
    try:
        for index, row in enumerate(rows):
            if row.get('kind') != 'server-cli':
                continue
            states = {'running', 'succeeded'}
            if index == 0 and pending_self:
                states |= {'accepted', 'queued'}
            if row.get('server_cli_cancel_requested') is not False or row.get('lifecycle') not in states:
                raise ServerCliLineageError('parent_cancelled_or_unavailable')
            dates = []
            if row.get('started_at') is not None:
                dates.append(_timestamp(row['started_at']) + timedelta(seconds=row['deadline_seconds']))
            elif row.get('lifecycle') not in {'accepted', 'queued'}:
                raise ValueError()
            proof = json.loads(row.get('execution_policy_provenance_json') or '{}')
            for name in ('server_cli.parent_deadline_at', 'server_cli.absolute_deadline_at'):
                if name in proof:
                    dates.append(_timestamp(proof[name]))
            for value in dates:
                deadline = value if deadline is None else min(deadline, value)
        if deadline is not None and datetime.now(timezone.utc) >= deadline:
            raise ServerCliLineageError('parent_deadline_exceeded')
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise ServerCliLineageError('parent_context_unavailable') from None
    return deadline
