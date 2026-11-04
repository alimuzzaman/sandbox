"""Allowlisted upload diagnostics and the public text/JSON failure contract."""
from __future__ import annotations

import re
import shlex

CODES=frozenset('unsafe_chunk_entry unsafe_chunk_cache duplicate_chunk_key chunk_manifest_invalid chunk_protocol_invalid chunk_streams_invalid chunk_session_unavailable chunk_duplicate_busy chunk_hash_mismatch bundle_hash_mismatch target_busy partial_cache_full chunk_writers_active chunk_load_unknown reservation_missing reservation_owner_mismatch reservation_store_unsafe reservation_ledger_unsafe reservation_ledger_invalid reservation_lock_busy chunk_no_space chunk_permission_denied chunk_missing chunk_timeout chunk_operation_failed'.split())
PHASES=frozenset('worker_start receiver_setup coordinator_setup chunk_receive manifest await_finalize bundle_verify docker_load cache_cleanup reservation_release'.split())
VALUES=frozenset('expected_mode actual_mode expected_uid actual_uid links index expected_bytes actual_bytes expected_sha256 actual_sha256 plans max_plans retained_bytes_limit cached_bytes missing_bytes active_bundle_bytes chunks verified_chunks docker_exit errno entries max_entries timeout_seconds'.split())
EFFECTS={'cache':{'unknown','unchanged','partial_may_exist','manifest_or_chunks_may_exist','removed'},
         'load':{'unknown','not_started','may_have_run','completed'},
         'reservation':{'unknown','retained','released'},'quarantine':{True,False},'cleanup':{'pending'}}


def worker_failure(value):
    if not isinstance(value,dict) or value.get('state')!='refused':return None
    if (set(value)!={'schema','state','code','phase','values','effects'} or type(value['schema']) is not int
            or value['schema']!=1 or not isinstance(value['code'],str) or value['code'] not in CODES or not isinstance(value['phase'],str) or value['phase'] not in PHASES
            or not isinstance(value['values'],dict) or not isinstance(value['effects'],dict)):
        return None
    for key,item in value['values'].items():
        if key not in VALUES:return None
        if key.endswith('sha256'):
            if item is not None and (not isinstance(item,str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',item)):return None
        elif type(item) is not int or not -2147483648<=item<=2**63-1:return None
    for key,item in value['effects'].items():
        if key not in EFFECTS or not isinstance(item,(str,bool)) or item not in EFFECTS[key]:return None
    if not {'cache','load','reservation'}<=value['effects'].keys():return None
    return {'phase':value['phase'],'values':value['values'],'effects':value['effects']}


def public_failure(error, *, receipt, remote, streams):
    detail=error.failure
    if not isinstance(detail,dict):return None
    code=error.code
    resume=shlex.join(['./sb','host','image','push-local','--remote',remote,'--receipt',str(receipt),
                       '--upload-streams',str(streams or 8),'--confirm','--json'])
    storage=shlex.join(['./sb','host','image','storage-admission','--remote',remote,'--receipt',str(receipt),'--json'])
    identity=shlex.join(['./sb','remote','service','status',remote,'--json'])
    # Diagnostics are executable inspection commands, never speculative chmod,
    # reservation deletion, Docker replay, or a quarantine bypass.
    cause=code.removeprefix('local_images_')
    if cause in ('chunk_no_space','partial_cache_full','chunk_permission_denied','unsafe_chunk_entry','unsafe_chunk_cache') or cause.startswith('reservation_'):
        command=storage
        description='Inspect the reported storage bytes/modes and ledger. An owner must repair the named storage or integrity cause; retry cannot repair permissions. Never delete an active reservation.'
    elif cause in ('chunk_hash_mismatch','bundle_hash_mismatch','chunk_manifest_invalid'):
        command=shlex.join(['python3','-c', 'import hashlib,sys; from sandbox.hosting.local_images import load_receipt; r,p=load_receipt(sys.argv[1]); [(print("expected="+b["archive_sha256"]),print("actual=sha256:"+hashlib.file_digest((p.parent/b["archive"]).open("rb"),"sha256").hexdigest())) for b in r["bundles"].values()]', str(receipt)])
        description='Verify the receipt and controller archives. Replace a damaged local bundle with a rebuilt, verified receipt; corrupt remote chunks are discarded on a rerun.'
    else:
        command=identity
        description='Inspect the registered host and runtime status. A lost SSH session may leave verified chunks. If load may have run, an owner must reconcile quarantine and Docker state before any replay.'
    return {'ok':False,'step':detail['phase'],'code':code,'why':detail['values'],
            'state':detail['effects'],'fix':{'command':command,'description':description},'resume':resume,
            'bypass':{'flag':None,'reason':'None: worker fencing, storage floor, ledger integrity and uncertain Docker loads protect the shared host.'}}


def render_failure(payload):
    import json
    return '\n'.join((f"What failed: {payload['step']} [{payload['code']}]",
        'Why: '+json.dumps(payload['why'],sort_keys=True),
        'State: '+json.dumps(payload['state'],sort_keys=True),
        'Fix: '+payload['fix']['description']+'\n'+payload['fix']['command'],
        'Resume: '+payload['resume'],'Bypass: none — '+payload['bypass']['reason']))
