"""Credential-free, allowlisted Docker review metadata, never raw inspect output."""
from __future__ import annotations

from typing import TypedDict


class EngineReview(TypedDict, total=False):
    engine_id: str
    aliases: list[str]
    digests: list[str]
    labels: dict[str, str]
    created_at: str | None
    image_id: str | None
    state: dict[str, str | int | bool]
    container_ids: list[str]
    driver: str | None
    options: dict[str, str]
    references_complete: bool
    restart_count: int | None
    health_status: str | None
    labels_redacted: bool


def review_metadata(kind: str, record: dict, containers: list[dict], containers_complete: bool = False) -> EngineReview:
    """Allow only review fields; exclude Env, commands, mounts and health output."""
    import re
    sensitive = re.compile(r'(?i)(token|password|passphrase|authorization|cookie|credential|secret|key)')
    def labels(values):
        if not isinstance(values, dict):
            return {}
        return {str(key): '[redacted]' if sensitive.search(str(key)) else
                re.sub(r'(?i)(?:[a-z]+://[^\s/@:]+:[^\s/@]+@|bearer\s+\S+|(?:token|password|secret|credential)\s*[=:]\s*\S+)', '[redacted]', str(value))
                for key, value in values.items() if isinstance(value, (str, int, bool))}
    identity = str(record.get('Id') or record.get('Name') or '')
    result = {'engine_id': identity, 'references_complete': bool(containers_complete),
              'created_at': record.get('Created') or record.get('CreatedAt'),
              'labels': labels((record.get('Config') or {}).get('Labels')
                               if kind in {'image', 'container'} else record.get('Labels'))}
    if kind == 'image':
        result.update(aliases=list(record.get('RepoTags') or ()), digests=list(record.get('RepoDigests') or ()),
                      container_ids=sorted({str(row['Id']) for row in containers
                                            if row.get('Image') == identity and row.get('Id')}))
    elif kind == 'container':
        state = record.get('State') or {}
        result.update(image_id=record.get('Image'), restart_count=record.get('RestartCount'),
                      health_status=(state.get('Health') or {}).get('Status'), state={key: state[key] for key in (
            'Status', 'Running', 'Paused', 'Restarting', 'OOMKilled', 'Dead', 'ExitCode',
            'StartedAt', 'FinishedAt') if key in state and isinstance(state[key], (str, bool, int))})
    elif kind == 'volume':
        result.update(driver=record.get('Driver'), options=labels(record.get('Options')),
                      container_ids=sorted({str(row['Id']) for row in containers if row.get('Id')
                          and any(mount.get('Type') == 'volume' and mount.get('Name') == identity
                                  for mount in row.get('Mounts') or ())}))
    result['labels_redacted'] = any(value == '[redacted]' or '[redacted]' in value
                                    for key in ('labels', 'options') for value in result.get(key, {}).values())
    return result
