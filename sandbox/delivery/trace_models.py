"""Closed diagnostic trace codecs. No owner state is loaded here."""
from __future__ import annotations
import copy
import json
import re
import time
import uuid
from . import models as m
from sandbox.hosting.recovery.models import validate_source_artifact

STAGES = ('bootstrap','preflight','release_selection','artifact','checkout','prepare','stage','activation','runtime_verification','public_verification','recovery')
STATUSES = {'not_started','running','succeeded','failed','unknown','not_applicable'}
EFFECTS = {'not_started','entered','observed','failed','unknown','not_applicable'}
COMMAND_RESULTS = {'not_finished','succeeded','failed','interrupted','unknown'}
DEPLOYMENT_RESULTS = {'not_started','incomplete','succeeded','failed','unknown'}
COMPLETENESS = m.STATES | {'omitted','retained','unavailable','present','unknown','complete'}
CODES = m.REASONS | {'source_recorded','source_unavailable','trace_retained','preflight_failed','preflight_succeeded','deployment_needs_inspection','command_completed','created','recorded','existing','trace_request_conflict','trace_expired','trace_capacity','trace_store_unavailable','trace_contract_invalid','trace_sequence_conflict','trace_revision_unsupported','trace_acceptance_unknown','trace_owner_conflict','trace_owner_expired','trace_owner_missing','trace_record_incomplete','trace_unavailable','history_unavailable','budget_exhausted','recorded','producer_recorded','owner_unavailable','missing','terminal_conflict'}

class TraceContractError(ValueError):
    def __init__(self, code='trace_contract_invalid'):
        self.code = code
        super().__init__(code)

def fail(code='trace_contract_invalid'):
    raise TraceContractError(code)

class TraceQueryBudget:
    def __init__(self, deadline_monotonic):
        self.deadline_monotonic = deadline_monotonic
    def remaining_seconds(self):
        return max(0.0, self.deadline_monotonic-time.monotonic())
    @property
    def expired(self):
        return self.remaining_seconds() <= 0
    def check(self):
        if self.expired:
            fail('budget_exhausted')

def encoded(value):
    try:
        return m.encoded(value)
    except (m.DeliveryError,ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        fail()

def closed(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        fail()
    return {key: validator(value[key]) for key,validator in fields.items()}

def optional_fields(value, fields):
    if not isinstance(value, dict) or set(value)-set(fields):
        fail()
    return {key: fields[key](item) for key,item in value.items()}

def uuid_value(value):
    try:
        if not isinstance(value,str) or str(uuid.UUID(value)) != value:
            fail()
    except (ValueError,AttributeError):
        fail()
    return value

def runtime_revision(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{24}',value):
        fail()
    return value

def observed_version(value):
    if not re.fullmatch(r'v?\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.-]+)?',m.text(value,100)):
        fail()
    return value

def version(value):
    if not re.fullmatch(r'v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?',m.text(value,100)):
        fail()
    return value

def reason(value):
    result=closed(value,{'code':m.choice(CODES),'message':lambda v:m.text(v,200)})
    # Do not retain arbitrary exception paths or secrets, even after redaction.
    if m.redact_text(result['message']) != result['message'] or re.search(r'https?://|(?:^|\s)/|[A-Za-z]:\\',result['message']):
        fail()
    if len(encoded(result))>256:
        fail()
    return result

def safe_reason(code):
    return {'code':code,'message':code.replace('_',' ')}

def bounded(value, parser, maximum):
    if len(encoded(value))>maximum:
        fail()
    try:
        return parser(value)
    except (m.DeliveryError,ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        fail()

def _input(value):
    if isinstance(value,str):
        if '\x00' in value or len(value.encode('utf-8'))>32768:
            fail()
        try:
            def pairs(items):
                result={}
                for key,item in items:
                    if key in result: fail()
                    result[key]=item
                return result
            value=json.loads(value,object_pairs_hook=pairs,parse_constant=lambda _:fail())
        except (ValueError,UnicodeError):
            fail()
    return value

PRODUCER = {'id':m.choice({'lenzora-hosted-v1'}),'version':m.schema,'projection_schema':m.schema}
PRODUCER_CONTEXT = {'source_commit':m.optional(m.revision),'source_digest':m.optional(m.digest),'node_version':m.optional(version),'state':m.choice({'known','partial'}),'reason':reason}
CONTROLLER_CONTEXT = {'source_commit':m.optional(m.revision),'source_runtime_revision':runtime_revision,'installed_controller_runtime_revision':m.optional(runtime_revision)}
TARGET = {'environment':m.identifier,'remote_name':m.optional(m.identifier)}
SOURCE = {'revision':m.optional(m.revision),'policy':m.choice({'retained_verified_artifact'})}
START_FIELDS = {'schema_version':m.schema,'producer':lambda v:closed(v,PRODUCER),'mode':m.choice({'preflight','deploy','legacy_projection'}),'requested_target':lambda v:closed(v,TARGET),'requested_source':lambda v:closed(v,SOURCE),'producer_context':lambda v:closed(v,PRODUCER_CONTEXT),'expected_runtime_revision':runtime_revision}

def parse_trace_start(value):
    return bounded(_input(value),lambda v:closed(v,START_FIELDS),32768)

STAGE_PAYLOAD = {'stage':m.choice(set(STAGES)),'status':m.choice(STATUSES),'effect_state':m.choice(EFFECTS),'reason':reason,'references':m.array(m.reference,4)}

def identity(value):
    if not isinstance(value,dict): fail()
    fields={
        'producer_source':{'source_commit':m.revision,'source_digest':m.digest},
        'controller':{'remote_name':m.identifier,'installed_controller_runtime_revision':runtime_revision},
        'application_source':{'revision':m.revision,'root_digest':m.digest},
    }.get(value.get('kind'))
    if fields is None: fail()
    return closed(value,{'kind':m.choice(set(['producer_source','controller','application_source'])),**fields})

def payload(value, owner=False):
    from .producers.lenzora import decode_owner_projection
    if not isinstance(value,dict): fail()
    kind=value.get('kind')
    fields={'stage':STAGE_PAYLOAD,'projection':{'projection':decode_owner_projection}}
    if owner:
        fields['stage']={**STAGE_PAYLOAD,'role':m.choice({'prepare_job','activation_job'}),'job_request_id':m.request_identifier}
    else:
        fields.update(identity={'identity':identity},finish={'command_result':m.choice(COMMAND_RESULTS-{'not_finished'}),'requested_deployment_result':m.choice(DEPLOYMENT_RESULTS),'reason':reason},gap={'stage':m.choice(set(STAGES)),'effect_state':m.choice(EFFECTS),'reason':reason,'references':m.array(m.reference,4)})
    if kind not in fields: fail()
    return closed(value,{'kind':m.choice(set(fields)),**fields[kind]})

def _record(value,owner=False):
    return bounded(_input(value),lambda v:closed(v,{'schema_version':m.schema,'producer_context':lambda x:closed(x,PRODUCER_CONTEXT),'expected_runtime_revision':runtime_revision,'payload':lambda x:payload(x,owner)}),32768)

def parse_trace_record(value): return _record(value)
def parse_trace_owner_record(value): return _record(value,True)

def stage(value):
    return closed(value,{'status':m.choice(STATUSES),'first_at':m.optional(m.timestamp),'last_at':m.optional(m.timestamp),'provenance':m.choice({'producer_recorded','owner_verified'}),'reason':reason,'references':m.array(m.reference,4),'effect_state':m.choice(EFFECTS)})

def empty_stage():
    return dict(status='not_started',first_at=None,last_at=None,provenance='producer_recorded',reason=safe_reason('none'),references=[],effect_state='not_started')

def role_stages(value):
    return closed(value,{'prepare_job':lambda v:closed(v,{k:stage for k in ('prepare','stage')}),'activation_job':lambda v:closed(v,{k:stage for k in ('activation','runtime_verification','public_verification')})})

def event(value):
    return bounded(value,lambda v:closed(v,{'seq':m.integer,'at':m.timestamp,**STAGE_PAYLOAD,'provenance':m.choice({'producer_recorded','owner_verified'})}),1024)

def _document(value):
    from .producers.lenzora import decode_owner_projection
    fields={'schema_version':m.schema,'trace_id':uuid_value,'trace_request_id':uuid_value,'intent_digest':m.digest,'project_identity':m.identifier,'project_root_digest':m.digest,'producer':lambda v:closed(v,PRODUCER),'requested':lambda v:closed(v,{'mode':START_FIELDS['mode'],'target':lambda x:closed(x,TARGET),'source':lambda x:closed(x,SOURCE)}),'producer_context':lambda v:closed(v,PRODUCER_CONTEXT),'controller_context':lambda v:closed(v,CONTROLLER_CONTEXT),'sequence':m.integer,'created_at':m.timestamp,'updated_at':m.timestamp,'finished_at':m.optional(m.timestamp),'lifecycle':m.choice({'open','terminal','unknown'}),'command_result':m.choice(COMMAND_RESULTS),'deployment_result':m.choice(DEPLOYMENT_RESULTS),'stages':lambda v:closed(v,{key:stage for key in STAGES}),'events':m.array(event,64),'omitted_events':m.integer,'parent_link':m.optional(lambda v:closed(v,{'producer_id':m.choice({'lenzora-hosted-v1'}),'parent_request_id':m.request_identifier,'intent_digest':m.digest})),'owner_projection':m.optional(decode_owner_projection),'candidate_links':m.array(m.reference,32),'recording_gap':m.optional(lambda v:closed(v,{'stage':m.choice(set(STAGES)),'effect_state':m.choice(EFFECTS),'reason':reason,'references':m.array(m.reference,4)})),'terminal_digest':m.optional(m.digest)}
    result=closed(value,fields)
    if (result['lifecycle']=='terminal') != (result['finished_at'] is not None and result['terminal_digest'] is not None): fail()
    return result

def parse_trace_document(value):
    result=bounded(value,_document,131072)
    if result['terminal_digest'] is not None and result['terminal_digest'] != m.canonical_digest({key:item for key,item in result.items() if key!='terminal_digest'}): fail()
    return result

def receipt(value):
    return closed(value,{'mutation_id':uuid_value,'content_digest':m.digest,'accepted_sequence':m.integer,'document_digest':m.digest})

# Native evidence remains dimension-specific; arbitrary native dictionaries never pass.
def role_detail(value):
    common={'source_kind':m.identifier,'observed_at':m.optional(m.timestamp),'target_digest':m.optional(m.digest),'applicability':m.choice({'required','optional','not_applicable'}),'state':m.choice(COMPLETENESS),'result':m.choice(m.RESULTS),'reason':reason}
    scalar={
        'source':{'requested_revision':m.revision,'observed_revision':m.revision,'control_revision':m.revision,'source_artifact':lambda artifact:validate_source_artifact(artifact,value.get('source',{}).get('control_revision'))},
        'artifact':{'policy':m.choice({'retained_verified_artifact'}),'receipt_digest':m.digest,'plan_digest':m.digest,'proof_digest':m.digest,'manifest_digests':m.array(m.digest,32)},
        'target':{'registered_host_digest':m.digest,'environment':m.identifier,'incarnation_id':m.identifier},
        'generation':{'generation_id':m.integer,'digest':m.digest},'configuration':{'digest':m.digest},
        'initializer':{'status':m.identifier,'receipt_digest':m.digest},
        'runtime':{'source_revision':m.revision,'image_digests':m.array(m.digest,32),'health':m.identifier,'verification_digest':m.digest},
        'public_verification':{'hosts':m.array(m.route_host,20),'edge_state':m.identifier,'edge_proof_digest':m.digest},
        'job':{'submission_digest':m.digest,'started_at':m.timestamp,'finished_at':m.timestamp,'exit_code':lambda v:v if type(v)is int and -255<=v<=255 else fail(),'output_complete':m.boolean},
    }
    return bounded(value,lambda v:closed(v,{key:(lambda x,f=fields:closed(x,{**common,**{n:m.optional(c) for n,c in f.items()}})) for key,fields in scalar.items()}),16384)

def _query(value):
    def detail(v):
        if not isinstance(v,dict): fail()
        clone=dict(v); coverage=clone.pop('detail_coverage',None)
        result=_document(clone)
        result['detail_coverage']=closed(coverage,{'retained_document_digest':m.digest,'projection':m.choice({'included','omitted','not_recorded'}),'retained_events':m.integer,'returned_events':m.integer,'omitted_events':m.integer})
        return result
    role={'role':m.identifier,'request_id':m.optional(m.request_identifier),'job_id':m.optional(m.identifier),'operation_id':m.optional(m.identifier),'source_role':m.choice({'control','application','original_deployment'}),'state':m.choice(COMPLETENESS),'result':m.choice(m.RESULTS),'provenance':m.choice({'producer_recorded','owner_verified'}),'observed_at':m.optional(m.timestamp),'proof_digest':m.optional(m.digest),'reason':reason,'detail':m.optional(role_detail)}
    return closed(value,{'schema_version':m.schema,'query_kind':m.choice({'deployment_trace'}),'ok':m.boolean,'error':m.optional(reason),'query_scope':lambda v:closed(v,{'project_identity':m.identifier,'project_root_digest':m.digest,'trace_id':m.optional(uuid_value),'trace_request_id':m.optional(uuid_value)}),'recorded_at':m.timestamp,'observation_mode':m.choice({'recorded_only'}),'trace':m.optional(detail),'owner_evidence':lambda v:closed(v,{'summary':lambda x:closed(x,{'joined_deployment_result':m.choice(DEPLOYMENT_RESULTS),'completeness':m.choice(COMPLETENESS),'reason':reason}),'links':m.array(lambda x:closed(x,role),32),'parent_stages':lambda x:closed(x,{'role_stages':m.optional(role_stages),'completeness':m.choice(COMPLETENESS),'reason':reason})}),'recovery_operations':lambda v:closed(v,{'completeness':m.choice(COMPLETENESS),'omitted':m.integer,'operations':m.array(m.validate_summary,10)}),'mutation_receipt':m.optional(receipt),'coverage':lambda v:closed(v,{'trace_state':m.choice(COMPLETENESS),'owner_state':m.choice(COMPLETENESS),'early_history':m.choice({'recorded','unavailable'}),'omitted_events':m.integer,'omitted_links':m.integer,'reason':reason}),'next_action':m.optional(lambda v:m.text(v,4096))})

def parse_trace_query(value): return bounded(value,_query,262144)

def serialize_trace_query(projection,max_bytes=262144):
    result=copy.deepcopy(projection)
    # Validate every field before elision; the storage codec is never changed.
    try: result=_query(result)
    except m.DeliveryError: fail()
    def fits(): return len(encoded(result))<=min(max_bytes,262144)
    if not fits() and result['trace']:
        result['trace']['owner_projection']=None
        result['trace']['detail_coverage']['projection']='omitted'
    while not fits() and (result['owner_evidence']['links'] or result['recovery_operations']['operations']):
        if result['recovery_operations']['operations']:
            result['recovery_operations']['operations'].pop();result['recovery_operations']['omitted']+=1
            result['recovery_operations']['completeness']='partial'
        else:
            result['owner_evidence']['links'].pop();result['coverage']['omitted_links']+=1
            result['owner_evidence']['summary'].update(joined_deployment_result='unknown',completeness='partial',reason=safe_reason('bounds'))
            result['coverage'].update(owner_state='partial',reason=safe_reason('bounds'))
    while not fits() and result['trace'] and result['trace']['events']:
        result['trace']['events'].pop(0)
        result['trace']['detail_coverage']['returned_events']-=1
        result['trace']['detail_coverage']['omitted_events']+=1
        result['coverage']['omitted_events']+=1
    if not fits(): fail()
    return encoded(result).decode('utf-8')
