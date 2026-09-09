"""Pure fixed decoder for W11 state.ts identities and nonsecret projections."""
import hashlib
import json
from .. import models as m
from ..trace_models import (bounded,closed,optional_fields,fail,reason,version,
                            runtime_revision,observed_version)

CHECK_IDS=('node','pnpm','git','gh','python','cosign','control','defaults','sandbox-source','sandbox-interfaces','sandbox-runtime')
CHECK_CODES={'verified','prerequisite_failed','tool_missing','tool_execution_failed','tool_timeout','tool_nonzero','tool_malformed','version_mismatch','control_missing','control_noncanonical','control_dirty','defaults_unsafe','defaults_malformed','defaults_ambiguous','sandbox_not_tracked','sandbox_not_executable','interfaces_missing','runtime_inactive','runtime_unauthenticated','runtime_malformed','runtime_unknown','runtime_mismatch'}

def safe_integer(value):
    if type(value)is not int or not 0<=value<=2**53-1: fail()
    return value

def positive(value):
    value=safe_integer(value)
    if not value: fail()
    return value

def release(value):
    result=closed(value,{'target':m.choice({'development','production'}),'revision':m.revision,'branch':m.choice({'dev','main'}),'run_id':positive,'run_attempt':positive,'artifact_id':positive,'artifact_name':m.identifier})
    if result['branch']!=('dev' if result['target']=='development' else 'main') or result['artifact_name']!=f"hosted-{result['target']}-images-{result['revision']}": fail()
    return result

def request_identity_bytes(selection,receipt_digest,generation):
    selection=release(selection);m.digest(receipt_digest);safe_integer(generation)
    # JS schema insertion order and JS number/string spelling are normative.
    js_release={js:selection[py] for js,py in [('target','target'),('revision','revision'),('branch','branch'),('runId','run_id'),('runAttempt','run_attempt'),('artifactId','artifact_id'),('artifactName','artifact_name')]}
    return json.dumps({'domain':'lenzora-hosted-deployment-v1','release':js_release,'receiptDigest':receipt_digest,'generation':generation},ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')

def parent_request_id(selection,receipt_digest,generation):
    prefix='lenzora-dev' if selection['target']=='development' else 'lenzora-prod'
    return prefix+'-'+hashlib.sha256(request_identity_bytes(selection,receipt_digest,generation)).hexdigest()[:40]

def run(value):
    result=closed(value,{'release':release,'receipt_digest':m.digest,'generation':safe_integer,'parent_request_id':m.identifier,'stage_request_id':m.identifier,'activation_request_id':m.identifier})
    parent=parent_request_id(result['release'],result['receipt_digest'],result['generation'])
    if result['parent_request_id']!=parent or result['stage_request_id']!=parent+'-stage' or result['activation_request_id']!=parent+'-activate': fail()
    return result

def preflight(value):
    def check(v):
        c=closed(v,{'id':m.choice(set(CHECK_IDS)),'status':m.choice({'passed','failed','blocked'}),'code':m.choice(CHECK_CODES),'observed':lambda x:optional_fields(x,{'version':observed_version,'revision':m.revision,'expectedVersion':version,'actualVersion':version,'localRuntimeRevision':runtime_revision,'installedRuntimeRevision':runtime_revision}),'blocked_by':m.array(m.choice(set(CHECK_IDS)),11)})
        if (c['status']=='passed')!=(c['code']=='verified') or (c['status']=='blocked')!=(c['code']=='prerequisite_failed'): fail()
        if bool(c['blocked_by'])!=(c['status']=='blocked') or c['id'] in c['blocked_by'] or len(c['blocked_by'])!=len(set(c['blocked_by'])): fail()
        return c
    p=closed(value,{'mode':m.choice({'preflight'}),'target':m.choice({'development','production'}),'requested_revision':m.optional(m.revision),'ok':m.boolean,'checks':m.array(check,16)})
    if [c['id'] for c in p['checks']]!=list(CHECK_IDS): fail()
    by_id={c['id']:c for c in p['checks']}
    for c in p['checks']:
        if any(by_id[dep]['status']=='passed' for dep in c['blocked_by']): fail()
    if p['ok']!=all(c['status']=='passed' for c in p['checks']): fail()
    return p

CONTROL={'source_commit':m.optional(m.revision),'root_digest':m.digest,'producer_source_digest':m.optional(m.digest),'state':m.choice(m.STATES),'reason':reason}
JOB={'role':m.choice({'prepare_job','activation_job'}),'job_id':m.identifier,'request_id':m.identifier,'control_root_digest':m.digest,'control_source_commit':m.revision,'submission_digest':m.digest,'state':m.choice(m.STATES),'reason':reason}
NATIVE={'role':m.choice({'stage_request','activation_operation','runtime','public_verification','recovery_operation'}),'request_id':m.request_identifier,'operation_id':m.optional(m.identifier),'generation':m.optional(safe_integer),'plan_digest':m.optional(m.digest),'proof_digest':m.optional(m.digest),'configuration_digest':m.optional(m.digest),'target_digest':m.optional(m.digest)}

def _projection(value):
    p=closed(value,{'schema_version':m.schema,'producer_id':m.choice({'lenzora-hosted-v1'}),'producer_version':m.schema,'projection_kind':m.choice({'current_run','legacy_run'}),'observed_at':m.timestamp,'preflight':m.optional(preflight),'run':m.optional(run),'control':lambda v:closed(v,CONTROL),'artifact':m.optional(lambda v:closed(v,{'policy':m.choice({'retained_verified_artifact'}),'revision':m.revision,'receipt_digest':m.digest,'plan_digest':m.optional(m.digest),'proof_digest':m.optional(m.digest),'configuration_digest':m.optional(m.digest),'manifest_digests':m.array(m.digest,32)})),'phase_jobs':m.array(lambda v:closed(v,JOB),2),'native_receipts':m.array(lambda v:closed(v,NATIVE),16),'terminal':m.optional(lambda v:closed(v,{'activation_request_id':m.identifier,'command_code':safe_integer,'claimed_outcome':m.choice({'succeeded','failed','unknown','incomplete'}),'receipt_digest':m.digest}))})
    r=p['run']
    if r is None:
        if p['artifact'] or p['phase_jobs'] or p['native_receipts'] or p['terminal']: fail()
        return p
    if p['preflight'] and p['preflight']['target']!=r['release']['target']: fail()
    if p['artifact'] and (p['artifact']['revision']!=r['release']['revision'] or p['artifact']['receipt_digest']!=r['receipt_digest']): fail()
    if len({j['role'] for j in p['phase_jobs']})!=len(p['phase_jobs']): fail()
    for j in p['phase_jobs']:
        if j['request_id']!=r['parent_request_id']+('-prepare' if j['role']=='prepare_job' else '-activate') or j['control_root_digest']!=p['control']['root_digest'] or j['control_source_commit']!=p['control']['source_commit']: fail()
    if len({(n['role'],n['request_id']) for n in p['native_receipts']})!=len(p['native_receipts']): fail()
    for n in p['native_receipts']:
        expected=r['stage_request_id'] if n['role']=='stage_request' else r['activation_request_id']
        if n['role']!='recovery_operation' and n['request_id']!=expected: fail()
        if n['generation'] is not None and n['role']!='recovery_operation' and n['generation']!=r['generation']: fail()
    if p['terminal'] and (p['terminal']['activation_request_id']!=r['activation_request_id'] or p['terminal']['receipt_digest']!=r['receipt_digest']): fail()
    return p

def decode_owner_projection(value):
    return bounded(value,_projection,32768)
