"""Bounded nonsecret server-CLI observations retained in original job policy.

This is a serialization contract, never execution/actor authority. Generic policy
strings remain limited to 128 bytes. Only the exact fields below have typed,
purpose-specific bounds, inside the unchanged 65536-byte original-job snapshot.
"""
from __future__ import annotations

import ipaddress
import json
from pathlib import PurePosixPath
import re

TARGET_SNAPSHOT_MAX_BYTES = 32768
TARGET_METADATA_MAX_BYTES = 512
TARGET_FIELDS = frozenset({'schema_version', 'target_identity', 'ownership_marker', 'runtime_revision',
    'origin_remote_name', 'project', 'environment', 'name', 'copy_id', 'source_commit',
    'source_origin_commit', 'source_dir', 'compose_files', 'native_keys', 'services', 'selector_digest'})


def _text(value, limit=4096):
    return (isinstance(value, str) and 0 < len(value.encode('utf-8')) <= limit
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value))


def routing_metadata(value):
    if not isinstance(value, dict) or set(value) - {'origin_ipv4', 'origin_ipv6', 'tailscale_host', 'front_door', 'mcp_port'}:
        raise ValueError('original target routing metadata is invalid')
    result = dict(value)
    for field, version in (('origin_ipv4', 4), ('origin_ipv6', 6)):
        if field in result:
            if not isinstance(result[field], str):
                raise ValueError('original target address metadata must be text')
            address = ipaddress.ip_address(result[field])
            if address.version != version:
                raise ValueError('original target address family changed')
            result[field] = str(address)
    if 'tailscale_host' in result:
        if not isinstance(result['tailscale_host'], str):
            raise ValueError('original Tailnet address must be text')
        address = ipaddress.ip_address(result['tailscale_host'])
        networks = (ipaddress.ip_network('100.64.0.0/10'), ipaddress.ip_network('fd7a:115c:a1e0::/48'))
        if not any(address.version == net.version and address in net for net in networks):
            raise ValueError('original Tailnet address is outside the Tailnet ranges')
        result['tailscale_host'] = str(address)
    if 'front_door' in result and result['front_door'] not in {'nginx', 'caddy'}:
        raise ValueError('original target front door is invalid')
    if 'mcp_port' in result and (type(result['mcp_port']) is not int or not 1 <= result['mcp_port'] <= 65535):
        raise ValueError('original control port metadata is invalid')
    return result


def target_snapshot(value):
    if not isinstance(value, dict) or set(value) != TARGET_FIELDS:
        raise ValueError('owner target metadata schema is invalid')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('owner target metadata schema is invalid')
    for field in ('target_identity', 'ownership_marker', 'runtime_revision'):
        if not isinstance(value[field], str) or re.fullmatch(r'[0-9a-f]{24}', value[field]) is None:
            raise ValueError('owner target identity is invalid')
    if not isinstance(value['origin_remote_name'], str) or re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', value['origin_remote_name']) is None:
        raise ValueError('owner target origin is invalid')
    for field in ('source_commit', 'source_origin_commit'):
        if not isinstance(value[field], str) or re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value[field]) is None:
            raise ValueError('owner target source evidence is unavailable')
    for field in ('project', 'environment'):
        if not _text(value[field], 128):
            raise ValueError('owner target tuple is invalid')
    if value['name'] is not None and (not isinstance(value['name'], str) or re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', value['name']) is None):
        raise ValueError('owner target name is invalid')
    if value['copy_id'] is not None and (not isinstance(value['copy_id'], str) or re.fullmatch(r'[0-9a-f]{8}', value['copy_id']) is None):
        raise ValueError('owner target lifetime is invalid')
    for field in ('compose_files', 'native_keys'):
        items = value[field]
        if (not isinstance(items, list) or not 1 <= len(items) <= 64
                or any(not _text(item) for item in items) or len(set(items)) != len(items)):
            raise ValueError('owner target aliases or paths are invalid')
    for path in [value['source_dir'], *value['compose_files']]:
        if not _text(path) or not PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts:
            raise ValueError('owner target measured paths are invalid')
    services = value['services']
    if not isinstance(services, list) or not 1 <= len(services) <= 64:
        raise ValueError('owner target services are unavailable')
    seen = set()
    for item in services:
        if (not isinstance(item, dict) or set(item) != {'name', 'index', 'container_id'}
                or not isinstance(item['name'], str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', item['name']) is None
                or type(item['index']) is not int or not 1 <= item['index'] <= 9999
                or not isinstance(item['container_id'], str) or re.fullmatch(r'[0-9a-f]{12,64}', item['container_id']) is None
                or (item['name'], item['index']) in seen):
            raise ValueError('owner target service metadata is invalid')
        seen.add((item['name'], item['index']))
    if not isinstance(value['selector_digest'], str) or re.fullmatch(r'sha256:[0-9a-f]{64}', value['selector_digest']) is None:
        raise ValueError('owner target selector digest is unavailable')
    if len(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()) > TARGET_SNAPSHOT_MAX_BYTES:
        raise ValueError('owner target snapshot exceeds its retained contract limit')
    return value


def _json_object(text, maximum):
    if not _text(text, maximum):
        raise ValueError('server CLI retained field exceeds its contract limit')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate retained contract key')
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique)


def retained_provenance_value(key, value, *, submission, provenance):
    """Return a validated exception field, or None for generic policy strings."""
    if key not in {'server_cli.target_snapshot', 'server_cli.target_metadata', 'server_cli.exec_target'}:
        return None
    if submission['kind'] != 'server-cli':
        raise ValueError('server CLI retained contract requires a server-cli job')
    if key == 'server_cli.target_metadata':
        routing_metadata(_json_object(value, TARGET_METADATA_MAX_BYTES))
    elif key == 'server_cli.exec_target':
        snapshot = _json_object(provenance.get('server_cli.target_snapshot'), TARGET_SNAPSHOT_MAX_BYTES)
        if (not _text(value, 257) or not isinstance(snapshot, dict)
                or not _text(snapshot.get('project'), 128) or not _text(snapshot.get('environment'), 128)
                or value != snapshot['project'] + '/' + snapshot['environment']):
            raise ValueError('server CLI retained exec target is invalid')
    else:
        snapshot = target_snapshot(_json_object(value, TARGET_SNAPSHOT_MAX_BYTES))
        for field in ('target_identity', 'ownership_marker', 'runtime_revision'):
            if snapshot[field] != provenance.get('server_cli.' + field):
                raise ValueError('retained target identity does not match original job')
        if (snapshot['origin_remote_name'] != provenance.get('server_cli.remote_name')
                or snapshot['selector_digest'] != provenance.get('server_cli.target_selector_digest')
                or snapshot['source_commit'] != submission['source']['commit']
                or snapshot['source_dir'] != submission['project_root']
                or snapshot['project'] + '/' + snapshot['environment'] != provenance.get('server_cli.exec_target')
                or not any(item['name'] == provenance.get('server_cli.exec_service') and item['index'] == 1
                           for item in snapshot['services'])):
            raise ValueError('retained target binding does not match original job')
    return value
