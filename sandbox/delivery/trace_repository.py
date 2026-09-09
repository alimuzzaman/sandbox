"""SQLite diagnostic owner. Permanent guards never authorize workload replay."""
from __future__ import annotations
import contextlib
import copy
import datetime as dt
import json
from pathlib import Path
import sqlite3
import uuid
from . import models as m
from .trace_models import (TraceContractError,TraceQueryBudget,STAGES,STATUSES,
    encoded,fail,safe_reason,uuid_value,parse_trace_start,parse_trace_record,
    parse_trace_owner_record,parse_trace_document,empty_stage,role_stages,
    PRODUCER_CONTEXT,CONTROLLER_CONTEXT,PRODUCER,TARGET,event,closed,reason)
from .producers.lenzora import decode_owner_projection

DB_BYTES=128*1024*1024
GUARD_LIMIT=4096
PROTECTED_LIMIT=128
TERMINAL_LIMIT=512
SCOPE_LIMIT=64
RECEIPT_LIMIT=256
DOCUMENT_BYTES=128*1024
RETENTION_DAYS=30

def _digest(value): return m.canonical_digest(value)
def _scope(scope):
    return {'project_identity':m.identifier(scope['project_identity']),'project_root_digest':m.digest(scope['project_root_digest'])}
def _locator(scope): return _digest({'domain':'sandbox-trace-project-v1',**_scope(scope)})
def _target(scope,intent):
    return _digest({'domain':'sandbox-trace-target-v1',**_scope(scope),'producer':intent['producer'],'target':intent['requested_target']})
def _now(): return m.now()

def _ack(code,trace_request_id,trace_id=None,sequence=None,document_digest=None):
    return dict(schema_version=1,ok=code in {'created','recorded','existing'},code=code,trace_id=trace_id,trace_request_id=trace_request_id,sequence=sequence,document_digest=document_digest,reason=safe_reason(code))
def _owner_ack(code,producer,parent,publication,sequence=None,document_digest=None):
    return dict(schema_version=1,ok=code in {'created','recorded','existing'},code=code,producer_id=producer,parent_request_id=parent,publication_id=publication,sequence=sequence,document_digest=document_digest,reason=safe_reason(code))

class TraceRepository:
    def __init__(self,home):
        self.path=Path(home)/'runtime'/'delivery'/'traces.sqlite3'

    @contextlib.contextmanager
    def _connection(self,write=False,budget=None,initialize=True):
        conn=None
        try:
            if budget: budget.check()
            if not write and not self.path.exists():
                yield None;return
            if write:
                if not initialize and not self.path.exists(): fail('trace_owner_missing')
                self.path.parent.mkdir(parents=True,exist_ok=True)
                for path in (self.path,Path(str(self.path)+'-journal')):
                    if path.exists() and path.stat().st_size>DB_BYTES: fail('trace_capacity')
            timeout=min(2.0,budget.remaining_seconds()) if budget else 2.0
            uri=self.path.resolve().as_uri()+('?mode=rw' if write and not initialize else '?mode=rwc' if write else '?mode=ro')
            conn=sqlite3.connect(uri,uri=True,timeout=timeout,isolation_level=None)
            conn.row_factory=sqlite3.Row
            if budget: conn.set_progress_handler(lambda:1 if budget.expired else 0,1000)
            if write:
                conn.execute('PRAGMA journal_mode=DELETE');conn.execute('PRAGMA synchronous=FULL')
                page=conn.execute('PRAGMA page_size').fetchone()[0]
                cap=(DB_BYTES-1024*1024)//page
                if conn.execute('PRAGMA max_page_count=%d'%cap).fetchone()[0]>cap: fail('trace_capacity')
                conn.execute('PRAGMA journal_size_limit=%d'%DB_BYTES)
                conn.execute('BEGIN IMMEDIATE')
            else:
                conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
            version=conn.execute('PRAGMA user_version').fetchone()[0]
            if write and initialize and version==0:
                if conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1").fetchone(): fail('trace_store_unavailable')
                for sql in (
                    'CREATE TABLE guards (kind TEXT NOT NULL, project TEXT NOT NULL, request TEXT NOT NULL, owner_id TEXT NOT NULL, target TEXT NOT NULL, intent TEXT NOT NULL, document BLOB NOT NULL, PRIMARY KEY(kind,project,request), UNIQUE(kind,project,owner_id))',
                    'CREATE TABLE details (kind TEXT NOT NULL, project TEXT NOT NULL, owner_id TEXT NOT NULL, target TEXT NOT NULL, document BLOB NOT NULL, protected INTEGER NOT NULL, finished_at TEXT, PRIMARY KEY(kind,project,owner_id))',
                    'CREATE INDEX details_retention ON details(kind,protected,target,finished_at)',
                    'CREATE TABLE receipts (kind TEXT NOT NULL, project TEXT NOT NULL, owner_id TEXT NOT NULL, receipt_id TEXT NOT NULL, content_digest TEXT NOT NULL, document BLOB NOT NULL, PRIMARY KEY(kind,project,owner_id,receipt_id))',
                ): conn.execute(sql)
                conn.execute('PRAGMA user_version=1')
            elif version!=1: fail('trace_revision_unsupported')
            yield conn
            if budget: budget.check()
            if write:
                if conn.execute('PRAGMA page_count').fetchone()[0]*conn.execute('PRAGMA page_size').fetchone()[0]>DB_BYTES-1024*1024: fail('trace_capacity')
                conn.execute('COMMIT')
        except sqlite3.Error as exc:
            if budget and budget.expired: fail('budget_exhausted')
            fail('trace_capacity' if getattr(exc,'sqlite_errorcode',None)==sqlite3.SQLITE_FULL else 'trace_store_unavailable')
        except OSError:
            fail('trace_store_unavailable')
        finally:
            if conn:
                if conn.in_transaction: conn.rollback()
                conn.close()

    def _decode(self,row,budget=None,limit=DOCUMENT_BYTES):
        if row is None: return None
        if budget: budget.check()
        raw=row['document']
        if not isinstance(raw,(bytes,str)) or len(raw)>limit: fail()
        try: value=json.loads(raw,parse_constant=lambda _:fail())
        except (ValueError,UnicodeError): fail()
        if budget: budget.check()
        return value

    def _get(self,conn,kind,project,owner,budget=None):
        value=self._decode(conn.execute('SELECT document FROM details WHERE kind=? AND project=? AND owner_id=?',(kind,project,owner)).fetchone(),budget)
        if value is not None and kind=='parent':
            value=closed(value,{'schema_version':m.schema,'producer_id':m.choice({'lenzora-hosted-v1'}),'parent_request_id':m.identifier,'intent_digest':m.digest,'sequence':m.integer,'created_at':m.timestamp,'updated_at':m.timestamp,'finished_at':m.optional(m.timestamp),'projection':decode_owner_projection,'role_stages':role_stages,'events':m.array(event,64),'omitted_events':m.integer,'producer_context':lambda v:closed(v,PRODUCER_CONTEXT),'controller_context':lambda v:closed(v,CONTROLLER_CONTEXT)})
            if value['parent_request_id']!=owner or value['projection']['run'] is None or value['projection']['run']['parent_request_id']!=owner or _digest(self._parent_identity(value['projection']))!=value['intent_digest']: fail()
        if budget: budget.check()
        return value

    def _guard(self,conn,kind,project,request=None,owner=None,budget=None):
        column,value=('request',request) if request is not None else ('owner_id',owner)
        guard=self._decode(conn.execute(f'SELECT document FROM guards WHERE kind=? AND project=? AND {column}=?',(kind,project,value)).fetchone(),budget,1024)
        if guard is None: return None
        common={'intent_digest':m.digest,'reserved_at':m.timestamp,'detail_state':m.choice({'present','expired','unknown'})}
        fields={**common,'trace_id':uuid_value,'trace_request_id':uuid_value,'producer':lambda v:closed(v,PRODUCER),'requested_target':lambda v:closed(v,TARGET),'initial_document_digest':m.digest} if kind=='trace' else {**common,'producer_id':m.choice({'lenzora-hosted-v1'}),'parent_request_id':m.identifier,'target_digest':m.digest}
        return closed(guard,fields)

    def _receipt(self,conn,kind,project,owner,receipt_id,budget=None):
        value=self._decode(conn.execute('SELECT document FROM receipts WHERE kind=? AND project=? AND owner_id=? AND receipt_id=?',(kind,project,owner,receipt_id)).fetchone(),budget,2048)
        if value is None: return None
        receipt_key='mutation_id' if kind=='trace' else 'publication_id'
        common={'schema_version':m.schema,'ok':m.boolean,
                'code':m.choice({'created','recorded','existing'}),
                'sequence':m.integer,'document_digest':m.digest,'reason':reason}
        ack_fields={**common,'trace_id':uuid_value,'trace_request_id':uuid_value} if kind=='trace' else {
            **common,'producer_id':m.choice({'lenzora-hosted-v1'}),
            'parent_request_id':m.identifier,'publication_id':uuid_value}
        result=closed(value,{receipt_key:uuid_value,'content_digest':m.digest,
            'accepted_sequence':m.integer,'document_digest':m.digest,
            'ack':lambda v:closed(v,ack_fields)})
        ack=result['ack']
        if (result[receipt_key]!=receipt_id or not ack['ok']
                or ack['sequence']!=result['accepted_sequence']
                or ack['document_digest']!=result['document_digest']
                or ack['trace_id' if kind=='trace' else 'parent_request_id']!=owner
                or kind=='parent' and ack['publication_id']!=receipt_id): fail()
        if budget: budget.check()
        return result

    def _expire(self,conn,row):
        kind,project,owner=row['kind'],row['project'],row['owner_id']
        guard=self._guard(conn,kind,project,owner=owner)
        guard['detail_state']='expired'
        conn.execute('UPDATE guards SET document=? WHERE kind=? AND project=? AND owner_id=?',(encoded(guard),kind,project,owner))
        conn.execute('DELETE FROM details WHERE kind=? AND project=? AND owner_id=?',(kind,project,owner))
        conn.execute('DELETE FROM receipts WHERE kind=? AND project=? AND owner_id=?',(kind,project,owner))

    def _prune(self,conn):
        cutoff=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=RETENTION_DAYS)).isoformat().replace('+00:00','Z')
        for kind in ('trace','parent'):
            rows=conn.execute('SELECT kind,project,owner_id,target,finished_at FROM details WHERE kind=? AND protected=0 ORDER BY finished_at DESC,owner_id DESC LIMIT 641',(kind,)).fetchall()
            counts={}
            for index,row in enumerate(rows):
                counts[row['target']]=counts.get(row['target'],0)+1
                if index>=TERMINAL_LIMIT or counts[row['target']]>SCOPE_LIMIT or row['finished_at']<cutoff: self._expire(conn,row)

    def _put(self,conn,kind,project,owner,target,document,protected=True):
        raw=encoded(document)
        if protected:
            existing=conn.execute('SELECT protected FROM details WHERE kind=? AND project=? AND owner_id=?',(kind,project,owner)).fetchone()
            if (existing is None or not existing['protected']) and conn.execute('SELECT count(*) FROM details WHERE kind=? AND protected=1',(kind,)).fetchone()[0]>=PROTECTED_LIMIT: fail('trace_capacity')
        if len(raw)>DOCUMENT_BYTES: fail('trace_capacity')
        conn.execute('INSERT INTO details VALUES (?,?,?,?,?,?,?) ON CONFLICT(kind,project,owner_id) DO UPDATE SET document=excluded.document,protected=excluded.protected,finished_at=excluded.finished_at',(kind,project,owner,target,raw,int(protected),document.get('finished_at')))
        if self._get(conn,kind,project,owner)!=document: fail('trace_acceptance_unknown')

    def _capacity(self,conn,kind,protected=True):
        if conn.execute('SELECT count(*) FROM guards').fetchone()[0]>=GUARD_LIMIT: fail('trace_capacity')
        if protected and conn.execute('SELECT count(*) FROM details WHERE kind=? AND protected=1',(kind,)).fetchone()[0]>=PROTECTED_LIMIT: fail('trace_capacity')

    def start(self,scope,trace_request_id,intent):
        try:
            uuid_value(trace_request_id);intent=parse_trace_start(intent);project=_locator(scope)
            controller=closed(scope['controller_context'],CONTROLLER_CONTEXT)
            if intent['expected_runtime_revision']!=controller['source_runtime_revision']: fail('trace_revision_unsupported')
            frozen={'scope':_scope(scope),'intent':intent,'controller_context':controller}
            intent_digest=_digest(frozen)
            with self._connection(True) as conn:
                guard=self._guard(conn,'trace',project,request=trace_request_id)
                if guard:
                    if guard['intent_digest']!=intent_digest: return _ack('trace_request_conflict',trace_request_id,guard['trace_id'])
                    doc=self._get(conn,'trace',project,guard['trace_id'])
                    if doc is None: return _ack('trace_expired',trace_request_id,guard['trace_id'])
                    return _ack('existing',trace_request_id,guard['trace_id'],0,guard['initial_document_digest'])
                self._prune(conn);self._capacity(conn,'trace')
                trace_id=str(uuid.uuid4());now=_now()
                doc=dict(schema_version=1,trace_id=trace_id,trace_request_id=trace_request_id,intent_digest=intent_digest,**_scope(scope),producer=intent['producer'],requested={'mode':intent['mode'],'target':intent['requested_target'],'source':intent['requested_source']},producer_context=intent['producer_context'],controller_context=controller,sequence=0,created_at=now,updated_at=now,finished_at=None,lifecycle='open',command_result='not_finished',deployment_result='not_started',stages={key:empty_stage() for key in STAGES},events=[],omitted_events=0,parent_link=None,owner_projection=None,candidate_links=[],recording_gap=None,terminal_digest=None)
                doc=parse_trace_document(doc);target=_target(scope,intent)
                guard=dict(trace_id=trace_id,trace_request_id=trace_request_id,intent_digest=intent_digest,producer=intent['producer'],requested_target=intent['requested_target'],reserved_at=now,detail_state='present',initial_document_digest=_digest(doc))
                if len(encoded(guard))>1024: fail('trace_capacity')
                conn.execute('INSERT INTO guards VALUES (?,?,?,?,?,?,?)',('trace',project,trace_request_id,trace_id,target,intent_digest,encoded(guard)))
                self._put(conn,'trace',project,trace_id,target,doc)
                if self._guard(conn,'trace',project,request=trace_request_id)!=guard: fail('trace_acceptance_unknown')
                return _ack('created',trace_request_id,trace_id,0,_digest(doc))
        except (TraceContractError,m.DeliveryError,ValueError,TypeError,KeyError) as exc:
            return _ack(exc.code if isinstance(exc,TraceContractError) else 'trace_contract_invalid',trace_request_id)

    def _context(self,scope,document,record):
        controller=closed(scope['controller_context'],CONTROLLER_CONTEXT)
        if controller['source_runtime_revision']!=record['expected_runtime_revision'] or controller['source_runtime_revision']!=document['controller_context']['source_runtime_revision'] or controller['source_commit']!=document['controller_context']['source_commit']: fail('trace_revision_unsupported')
        incoming=record['producer_context'];saved=document['producer_context']
        for key in ('source_commit','source_digest','node_version'):
            if saved[key] is not None and incoming[key]!=saved[key]: fail('trace_revision_unsupported')

    def _transition(self,document,stage_map,payload,owner_observation=None):
        name=payload['stage'];old=stage_map[name]
        observation={key:payload[key] for key in ('status','effect_state','reason','references')}
        if all(old[key]==value for key,value in observation.items()): return
        allowed={'not_started':{'running','failed','unknown','not_applicable'},'running':{'running','succeeded','failed','unknown'},'unknown':{'unknown'}}
        resolved=bool(owner_observation and owner_observation['completeness']=='complete' and old['references'] and old['references']==payload['references'])
        if resolved:
            allowed['unknown']={'unknown','running','succeeded','failed'}
        if payload['status'] not in allowed.get(old['status'],set()): fail('trace_sequence_conflict')
        if payload['status']=='not_applicable' and payload['reason']['code']!='not_applicable': fail()
        if old['effect_state']=='failed' and payload['effect_state']!='failed': fail('trace_sequence_conflict')
        if old['effect_state']=='unknown' and payload['effect_state']!='unknown' and not resolved: fail('trace_sequence_conflict')
        # Producer reports cannot resolve uncertainty into authoritative proof.
        if old['effect_state']=='observed' and payload['effect_state'] in {'not_started','entered'}: fail('trace_sequence_conflict')
        if old['effect_state']=='entered' and payload['effect_state']=='not_started': fail('trace_sequence_conflict')
        now=_now()
        stage_map[name]={**observation,'first_at':old['first_at'] or now,'last_at':now,'provenance':'producer_recorded'}
        item=dict(seq=document['sequence']+1,at=now,stage=name,**observation,provenance='producer_recorded')
        if len(encoded(item))>1024: fail()
        document['events'].append(item)
        if len(document['events'])>64:
            document['events'].pop(0);document['omitted_events']+=1

    def _parent_identity(self,projection):
        return {'run':projection['run'],'control':{key:projection['control'][key] for key in ('root_digest','source_commit','producer_source_digest')}}

    def _merge(self,old,new):
        """Monotonic proof enrichment; no partial report clears retained values."""
        if new is None: return copy.deepcopy(old)
        if old is None: return copy.deepcopy(new)
        if isinstance(old,dict) and isinstance(new,dict):
            result=copy.deepcopy(old)
            for key,value in new.items():
                if key=='observed_at': result[key]=max(old[key],value)
                elif key=='preflight':
                    result[key]=copy.deepcopy(old[key] or value)
                elif key=='projection_kind':
                    result[key]='current_run' if 'current_run' in (old[key],value) else 'legacy_run'
                elif key=='state':
                    if old[key]!='known': result[key]=value
                elif key=='reason':
                    if old.get('state')!='known': result[key]=value
                else: result[key]=self._merge(old[key],value)
            return result
        if isinstance(old,list) and isinstance(new,list):
            result=copy.deepcopy(old)
            for item in new:
                if isinstance(item,dict) and 'role' in item:
                    key=(item['role'],item.get('request_id'))
                    found=next((i for i,v in enumerate(result) if (v['role'],v.get('request_id'))==key),None)
                    if found is None:
                        if item['role']!='recovery_operation' and any(v['role']==item['role'] for v in result): fail('trace_owner_conflict')
                        result.append(item)
                    else: result[found]=self._merge(result[found],item)
                elif item not in result: result.append(item)
            return result
        if old!=new: fail('trace_owner_conflict')
        return old

    def _register_parent(self,conn,scope,document,projection):
        retained=document['owner_projection']
        if retained is not None and retained['run'] is not None and retained['run']!=projection['run']: fail('trace_owner_conflict')
        if projection['run'] is None:
            if document['parent_link'] is not None: fail('trace_owner_conflict')
            return
        project=_locator(scope);parent=projection['run']['parent_request_id'];producer=projection['producer_id'];request=producer+':'+parent
        if projection['control']['root_digest']!=scope['project_root_digest']: fail('trace_owner_conflict')
        if projection['run']['release']['target']!=document['requested']['target']['environment']: fail('trace_owner_conflict')
        if document['requested']['source']['revision'] not in (None,projection['run']['release']['revision']): fail('trace_revision_unsupported')
        if projection['projection_kind']!='legacy_run' and document['producer_context']['source_commit'] is not None and projection['control']['source_commit']!=document['producer_context']['source_commit']: fail('trace_revision_unsupported')
        intent=_digest(self._parent_identity(projection));guard=self._guard(conn,'parent',project,request=request)
        link=document['parent_link']
        linked_intent=guard['intent_digest'] if projection['projection_kind']=='legacy_run' and guard else intent
        if link is not None and link!={'producer_id':producer,'parent_request_id':parent,'intent_digest':linked_intent}: fail('trace_owner_conflict')
        if projection['projection_kind']=='legacy_run':
            if guard and self._get(conn,'parent',project,parent) is not None:
                retained=self._get(conn,'parent',project,parent)
                if retained['projection']['run']==projection['run']:
                    document['parent_link']={'producer_id':producer,'parent_request_id':parent,'intent_digest':guard['intent_digest']}
                    self._put(conn,'parent',project,parent,guard['target_digest'],retained)
            return
        if guard:
            if guard['intent_digest']!=intent: fail('trace_owner_conflict')
            parent_doc=self._get(conn,'parent',project,parent)
            if parent_doc is None:
                if projection['projection_kind']=='legacy_run': return
                fail('trace_owner_expired')
            merged=decode_owner_projection(self._merge(parent_doc['projection'],projection))
            if merged!=parent_doc['projection']:
                parent_doc['projection']=merged;parent_doc['sequence']+=1;parent_doc['updated_at']=_now()
            self._put(conn,'parent',project,parent,guard['target_digest'],parent_doc)
        else:
            self._capacity(conn,'parent');now=_now()
            target=_digest({'scope':_scope(scope),'producer':producer,'environment':projection['run']['release']['target']})
            guard=dict(producer_id=producer,parent_request_id=parent,intent_digest=intent,target_digest=target,reserved_at=now,detail_state='present')
            parent_doc=dict(schema_version=1,producer_id=producer,parent_request_id=parent,intent_digest=intent,sequence=0,created_at=now,updated_at=now,finished_at=None,projection=projection,role_stages={'prepare_job':{key:empty_stage() for key in ('prepare','stage')},'activation_job':{key:empty_stage() for key in ('activation','runtime_verification','public_verification')}},events=[],omitted_events=0,producer_context=copy.deepcopy(document['producer_context']),controller_context=copy.deepcopy(document['controller_context']))
            conn.execute('INSERT INTO guards VALUES (?,?,?,?,?,?,?)',('parent',project,request,parent,target,intent,encoded(guard)))
            self._put(conn,'parent',project,parent,target,parent_doc)
        document['parent_link']={'producer_id':producer,'parent_request_id':parent,'intent_digest':intent}

    def _observation(self,scope):
        value=scope.get('owner_observation')
        if value is None: return None
        return closed(value,{'joined_deployment_result':m.choice({'not_started','incomplete','succeeded','failed','unknown'}),'completeness':m.choice({'complete','partial','missing','expired','conflicting','unsupported'}),'children_terminal':m.boolean})

    def _reconcile_parent(self,conn,scope,parent):
        observation=self._observation(scope)
        if not observation or observation['completeness']!='complete' or not observation['children_terminal']: return
        project=_locator(scope)
        rows=conn.execute("SELECT document FROM details WHERE kind='trace' AND protected=1 LIMIT 129").fetchall()
        if len(rows)>128: fail('trace_capacity')
        for row in rows:
            trace=parse_trace_document(self._decode(row))
            if _locator(trace)==project and trace['parent_link'] and trace['parent_link']['parent_request_id']==parent:
                if trace['lifecycle']!='terminal': return
                # Retention metadata may change; immutable terminal bytes do not.
                conn.execute("UPDATE details SET protected=0 WHERE kind='trace' AND project=? AND owner_id=?",(project,trace['trace_id']))
        document=self._get(conn,'parent',project,parent)
        if document is None: return
        guard=self._guard(conn,'parent',project,owner=parent)
        document['finished_at']=document['finished_at'] or _now()
        self._put(conn,'parent',project,parent,guard['target_digest'],document,False)

    def _apply(self,conn,scope,document,record):
        p=record['payload'];kind=p['kind']
        if kind=='stage':
            name=p['stage'];mode=document['requested']['mode']
            if mode=='legacy_projection' or mode=='preflight' and name not in {'bootstrap','preflight'}: fail()
            if name not in {'bootstrap','recovery'} and p['status']!='not_applicable':
                if name!='preflight' and (document['producer_context']['source_commit'] is None or document['producer_context']['source_digest'] is None): fail('trace_revision_unsupported')
                for earlier in STAGES[:STAGES.index(name)]:
                    if document['stages'][earlier]['status'] not in {'succeeded','not_applicable'}: fail('trace_sequence_conflict')
            self._transition(document,document['stages'],p,self._observation(scope))
        elif kind=='projection':
            projection=p['projection']
            if (document['requested']['mode']=='legacy_projection')!=(projection['projection_kind']=='legacy_run'): fail()
            self._register_parent(conn,scope,document,projection)
            document['owner_projection']=projection
        elif kind=='identity':
            ident=p['identity']
            if ident['kind']=='producer_source':
                for key in ('source_commit','source_digest'):
                    if document['producer_context'][key] not in (None,ident[key]): fail('trace_revision_unsupported')
                    document['producer_context'][key]=ident[key]
            elif ident['kind']=='controller':
                context=document['controller_context'];target=document['requested']['target']
                if context['installed_controller_runtime_revision'] not in (None,ident['installed_controller_runtime_revision']) or target['remote_name'] not in (None,ident['remote_name']): fail('trace_revision_unsupported')
                context['installed_controller_runtime_revision']=ident['installed_controller_runtime_revision'];target['remote_name']=ident['remote_name']
            else:
                if document['requested']['source']['revision'] not in (None,ident['revision']): fail('trace_revision_unsupported')
                projection=document['owner_projection']
                if projection is not None and projection['run'] is not None and projection['run']['release']['revision']!=ident['revision']: fail('trace_revision_unsupported')
                document['requested']['source']['revision']=ident['revision']
                ref={'kind':'application_source','digest':ident['root_digest']}
                old=[r for r in document['candidate_links'] if r['kind']=='application_source']
                if old and old!=[ref]: fail('trace_revision_unsupported')
                if not old: document['candidate_links'].append(ref)
        elif kind=='gap': document['recording_gap']={key:value for key,value in p.items() if key!='kind'}
        else:
            document['command_result']=p['command_result']
            # Producer claims never establish owner-verified deployment success.
            document['deployment_result']='not_started' if document['requested']['mode']=='preflight' or document['owner_projection'] is None and not any(document['stages'][key]['effect_state'] in {'entered','observed','failed','unknown'} for key in STAGES[2:-1]) else 'failed' if p['requested_deployment_result']=='failed' else 'incomplete'
            observation=self._observation(scope)
            if observation and observation['completeness']=='complete' and observation['children_terminal'] and document['requested']['mode']=='deploy':
                document['deployment_result']=observation['joined_deployment_result']
            document['lifecycle']='terminal';document['finished_at']=_now()

    def record(self,scope,trace_id,mutation_id,expected_sequence,record):
        request=None
        try:
            uuid_value(trace_id);uuid_value(mutation_id);m.integer(expected_sequence);record=parse_trace_record(record);project=_locator(scope);content=_digest(record)
            with self._connection(True) as conn:
                guard=self._guard(conn,'trace',project,owner=trace_id)
                if guard is None: fail('trace_expired')
                request=guard['trace_request_id'];document=self._get(conn,'trace',project,trace_id)
                if document is None: fail('trace_expired')
                document=parse_trace_document(document)
                existing=self._receipt(conn,'trace',project,trace_id,mutation_id)
                if existing:
                    if existing['content_digest']!=content: fail('trace_request_conflict')
                    return existing['ack']
                if expected_sequence!=document['sequence']: fail('trace_sequence_conflict')
                self._context(scope,document,record)
                if document['lifecycle']=='terminal':
                    prior=conn.execute('SELECT document FROM receipts WHERE kind=? AND project=? AND owner_id=? AND content_digest=? LIMIT 1',('trace',project,trace_id,content)).fetchone()
                    if record['payload']['kind']=='finish' and prior:
                        original=self._decode(prior,limit=2048)
                        if conn.execute('SELECT count(*) FROM receipts WHERE kind=? AND project=? AND owner_id=?',('trace',project,trace_id)).fetchone()[0]>=RECEIPT_LIMIT: fail('trace_capacity')
                        replay={**original,'mutation_id':mutation_id}
                        conn.execute('INSERT INTO receipts VALUES (?,?,?,?,?,?)',('trace',project,trace_id,mutation_id,content,encoded(replay)))
                        if self._receipt(conn,'trace',project,trace_id,mutation_id)!=replay: fail('trace_acceptance_unknown')
                        return original['ack']
                    fail('trace_request_conflict')
                if conn.execute('SELECT count(*) FROM receipts WHERE kind=? AND project=? AND owner_id=?',('trace',project,trace_id)).fetchone()[0]>=RECEIPT_LIMIT: fail('trace_capacity')
                self._apply(conn,scope,document,record)
                document['sequence']+=1;document['updated_at']=_now()
                if document['lifecycle']=='terminal': document['terminal_digest']=_digest({key:value for key,value in document.items() if key!='terminal_digest'})
                document=parse_trace_document(document)
                observation=self._observation(scope)
                terminal_children=observation and observation['completeness']=='complete' and observation['children_terminal']
                protected=document['lifecycle']!='terminal' or (document['parent_link'] is not None and not terminal_children)
                # Without authoritative child reads parent-linked traces stay protected.
                target=_target(scope,{'producer':guard['producer'],'requested_target':guard['requested_target']})
                self._put(conn,'trace',project,trace_id,target,document,protected)
                if document['parent_link']:
                    self._reconcile_parent(conn,scope,document['parent_link']['parent_request_id'])
                ack=_ack('recorded',request,trace_id,document['sequence'],_digest(document))
                receipt=dict(mutation_id=mutation_id,content_digest=content,accepted_sequence=document['sequence'],document_digest=_digest(document),ack=ack)
                conn.execute('INSERT INTO receipts VALUES (?,?,?,?,?,?)',('trace',project,trace_id,mutation_id,content,encoded(receipt)))
                if self._receipt(conn,'trace',project,trace_id,mutation_id)!=receipt: fail('trace_acceptance_unknown')
                self._prune(conn)
                return ack
        except (TraceContractError,m.DeliveryError,ValueError,TypeError,KeyError) as exc:
            return _ack(exc.code if isinstance(exc,TraceContractError) else 'trace_contract_invalid',request,trace_id)

    def record_owner(self,scope,producer_id,parent_request_id,publication_id,expected_sequence,record):
        try:
            if producer_id!='lenzora-hosted-v1': fail()
            m.identifier(parent_request_id);uuid_value(publication_id);m.integer(expected_sequence);record=parse_trace_owner_record(record);project=_locator(scope);content=_digest(record)
            with self._connection(True,initialize=False) as conn:
                guard=self._guard(conn,'parent',project,request=producer_id+':'+parent_request_id)
                if guard is None: fail('trace_owner_missing')
                document=self._get(conn,'parent',project,parent_request_id)
                if document is None: fail('trace_owner_expired')
                existing=self._receipt(conn,'parent',project,parent_request_id,publication_id)
                if existing:
                    if existing['content_digest']!=content: fail('trace_owner_conflict')
                    return existing['ack']
                if expected_sequence!=document['sequence']: fail('trace_sequence_conflict')
                self._context(scope,document,record)
                if conn.execute('SELECT count(*) FROM receipts WHERE kind=? AND project=? AND owner_id=?',('parent',project,parent_request_id)).fetchone()[0]>=RECEIPT_LIMIT: fail('trace_capacity')
                p=record['payload']
                if p['kind']=='projection':
                    if _digest(self._parent_identity(p['projection']))!=guard['intent_digest']: fail('trace_owner_conflict')
                    document['projection']=decode_owner_projection(self._merge(document['projection'],p['projection']))
                else:
                    role=p['role'];name=p['stage']
                    if name not in document['role_stages'][role] or p['job_request_id']!=parent_request_id+('-prepare' if role=='prepare_job' else '-activate'): fail('trace_owner_conflict')
                    if document['producer_context']['source_commit'] is None or document['producer_context']['source_digest'] is None: fail('trace_revision_unsupported')
                    self._transition(document,document['role_stages'][role],p,self._observation(scope))
                document['sequence']+=1;document['updated_at']=_now()
                self._put(conn,'parent',project,parent_request_id,guard['target_digest'],document)
                self._reconcile_parent(conn,scope,parent_request_id)
                document=self._get(conn,'parent',project,parent_request_id)
                ack=_owner_ack('recorded',producer_id,parent_request_id,publication_id,document['sequence'],_digest(document))
                receipt=dict(publication_id=publication_id,content_digest=content,accepted_sequence=document['sequence'],document_digest=_digest(document),ack=ack)
                conn.execute('INSERT INTO receipts VALUES (?,?,?,?,?,?)',('parent',project,parent_request_id,publication_id,content,encoded(receipt)))
                if self._receipt(conn,'parent',project,parent_request_id,publication_id)!=receipt: fail('trace_acceptance_unknown')
                return ack
        except (TraceContractError,m.DeliveryError,ValueError,TypeError,KeyError) as exc:
            return _owner_ack(exc.code if isinstance(exc,TraceContractError) else 'trace_contract_invalid',producer_id,parent_request_id,publication_id)

    def read(self,scope,trace_id=None,trace_request_id=None,mutation_id=None,budget=None):
        result=dict(state='missing',trace=None,guard=None,mutation_receipt=None,reason=safe_reason('missing'))
        try:
            if (trace_id is None)==(trace_request_id is None) or mutation_id is not None and trace_id is None: fail()
            uuid_value(trace_id or trace_request_id)
            if mutation_id: uuid_value(mutation_id)
            project=_locator(scope)
            with self._connection(budget=budget) as conn:
                if conn is None: return result
                guard=self._guard(conn,'trace',project,request=trace_request_id,owner=trace_id,budget=budget)
                if guard is None: return result
                result['guard']=guard
                document=self._get(conn,'trace',project,guard['trace_id'],budget)
                if document is None:
                    result.update(state='expired',reason=safe_reason('trace_expired'));return result
                if budget: budget.check()
                document=parse_trace_document(document)
                if (any(document[key]!=guard[key] for key in ('trace_id','trace_request_id','intent_digest'))
                        or _scope(document)!=_scope(scope)): fail()
                result.update(state='retained',trace=document,reason=safe_reason('known'))
                if mutation_id:
                    receipt=self._receipt(conn,'trace',project,guard['trace_id'],mutation_id,budget)
                    result['mutation_receipt']={key:value for key,value in receipt.items() if key!='ack'} if receipt else None
                if budget: budget.check()
                return result
        except (TraceContractError,m.DeliveryError,ValueError,TypeError,KeyError) as exc:
            result.update(state='partial',reason=safe_reason(exc.code if isinstance(exc,TraceContractError) else 'trace_contract_invalid'))
            return result

    def read_owner(self,scope,producer_id,parent_request_id,publication_id=None,budget=None):
        result=dict(schema_version=1,ok=True,error=None,producer_id=producer_id,parent_request_id=parent_request_id,record=None,publication_receipt=None,coverage={'state':'missing','reason':safe_reason('trace_owner_missing')})
        try:
            if producer_id!='lenzora-hosted-v1': fail()
            m.identifier(parent_request_id)
            if publication_id: uuid_value(publication_id)
            project=_locator(scope)
            with self._connection(budget=budget) as conn:
                if conn is None: return result
                guard=self._guard(conn,'parent',project,request=producer_id+':'+parent_request_id,budget=budget)
                if guard is None: return result
                document=self._get(conn,'parent',project,parent_request_id,budget)
                if document is None:
                    result['coverage']={'state':'expired','reason':safe_reason('trace_owner_expired')};return result
                document['projection']=decode_owner_projection(document['projection']);document['role_stages']=role_stages(document['role_stages'])
                result['record']=document;result['coverage']={'state':'retained','reason':safe_reason('known')}
                if publication_id:
                    receipt=self._receipt(conn,'parent',project,parent_request_id,publication_id,budget)
                    result['publication_receipt']={key:value for key,value in receipt.items() if key!='ack'} if receipt else None
                if budget: budget.check()
                return result
        except (TraceContractError,m.DeliveryError,ValueError,TypeError,KeyError) as exc:
            error=safe_reason(exc.code if isinstance(exc,TraceContractError) else 'trace_contract_invalid')
            result.update(ok=False,error=error,coverage={'state':'partial','reason':error})
            return result
