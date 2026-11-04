"""Bounded parallel v2 chunks. One coordinator lends its exclusive fence fd."""
from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import shlex
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

from sandbox.core import _remote
from sandbox.hosting import local_images, chunk_upload
from sandbox.resources.storage_floor import RESERVATION_RELEASE_PROGRAM
from sandbox.transports.remote_local_images import RemoteLocalImageError
from sandbox.transports import remote_local_images as transport

CHUNK_BYTES=8*1048576
PROGRAM=RESERVATION_RELEASE_PROGRAM+chunk_upload.RUNTIME_PROGRAM


def manifest_for(archive: Path, context: dict) -> dict:
    whole=hashlib.sha256();chunks=[];offset=0
    with archive.open('rb') as source:
        while True:
            data=source.read(CHUNK_BYTES)
            if not data:break
            whole.update(data)
            chunks.append({'index':len(chunks),'offset':offset,'bytes':len(data),'sha256':'sha256:'+hashlib.sha256(data).hexdigest()})
            offset+=len(data)
    if offset!=context['archive_bytes'] or 'sha256:'+whole.hexdigest()!=context['bundle_digest'] or len(chunks)>8192:
        raise RemoteLocalImageError('local_images_archive_changed')
    result={'schema':1,'context':context,'chunk_bytes':CHUNK_BYTES,'chunks':chunks}
    if len(json.dumps(result).encode())>2097151:raise RemoteLocalImageError('local_images_receipt_invalid')
    return result


class Session:
    def __init__(self,argv,manifest,streams,timeout,cancelled):
        self.argv=argv;self.manifest=manifest;self.streams=streams;self.timeout=timeout;self.cancelled=cancelled
        self.process=None;self.timer=None;self.pending=b'';self.deadline=time.monotonic()+timeout
    def read(self):
        assert self.process is not None
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout,selectors.EVENT_READ)
            while b'\n' not in self.pending:
                if time.monotonic()>=self.deadline or (self.cancelled and self.cancelled()):
                    raise RemoteLocalImageError('local_images_transfer_unknown','Chunk session interrupted; retry same receipt after reconciliation; use --upload-streams 1 for serial.')
                if not selector.select(min(.1,max(.001,self.deadline-time.monotonic()))):continue
                data=os.read(self.process.stdout.fileno(),65536)
                if not data:raise RemoteLocalImageError('local_images_transfer_unknown','Chunk coordinator exited; preserve cache and reconcile before same-receipt retry.')
                self.pending+=data
                if len(self.pending)>1048576:raise RemoteLocalImageError('local_images_chunk_protocol_unsupported')
        raw,self.pending=self.pending.split(b'\n',1)
        try:return local_images._json_loads_closed(raw)
        except (ValueError,UnicodeError):raise RemoteLocalImageError('local_images_chunk_protocol_unsupported') from None
    def __enter__(self):
        try:
            self.process=subprocess.Popen(self.argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            self.timer=threading.Timer(self.timeout,self.process.kill);self.timer.start()
            self.process.stdin.write(json.dumps(self.manifest,sort_keys=True,separators=(',',':')).encode()+b'\n');self.process.stdin.flush()
            value=self.read();mid=local_images.canonical_digest(self.manifest)[7:]
            if (not isinstance(value,dict) or set(value)!={'schema','state','manifest_id','token','streams','inventory'}
                    or type(value['schema']) is not int or value['schema']!=1 or value['state']!='chunk-session'
                    or value['manifest_id']!=mid or not isinstance(value['token'],str) or not re.fullmatch(r'[a-f0-9]{32}',value['token'])
                    or type(value['streams']) is not int or value['streams']!=self.streams
                    or not isinstance(value['inventory'],list) or any(type(i) is not int or not 0<=i<len(self.manifest['chunks']) for i in value['inventory'])
                    or len(set(value['inventory']))!=len(value['inventory'])):
                raise RemoteLocalImageError('local_images_chunk_protocol_unsupported','Worker refused chunk protocol or capacity; no archive bytes sent. Inspect limits, then retry same receipt with --upload-streams 1; no legacy fallback.')
            self.ready=value;return self
        except BaseException:
            self.stop();raise
    def stop(self):
        if self.timer:self.timer.cancel()
        if self.process:
            try:self.process.stdin.close()
            except (BrokenPipeError,OSError):pass
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.wait(timeout=5)
            self.process.stdout.close()
    def __exit__(self,*_args):self.stop();return False
    def finalize(self):
        self.process.stdin.write(json.dumps({'schema':1,'action':'finalize','manifest_id':self.ready['manifest_id']}).encode()+b'\n');self.process.stdin.flush()
        value=self.read()
        expected={'schema':1,'state':'loaded','bytes':self.manifest['context']['archive_bytes']}
        if value!=expected:raise RemoteLocalImageError('local_images_transfer_unknown','Chunk finalization unverified; inspect quarantine and same receipt before retry; no automatic retry.')
        return value


def load_chunks(entry,archive,context,reservation,*,timeout,streams,progress=None,cancelled=None,on_started=None):
    if type(streams) is not int or not 1<=streams<=8:raise RemoteLocalImageError('local_images_upload_streams_invalid','--upload-streams must be 1–8; default 8, serial fallback 1.')
    started=time.monotonic();deadline=started+timeout;manifest=manifest_for(archive,context)
    def budget():
        left=deadline-time.monotonic()
        if left<=0:raise RemoteLocalImageError('local_images_transfer_unknown')
        return left
    root=transport._local_image_root(entry)
    args=['python3','-c',PROGRAM,'coordinate',root,reservation,str(max(1,int(budget()))),str(streams)]
    cancelled_all=threading.Event();lock=threading.Lock();sent={};completed=set();last_report=0.;active=0
    def stopped():return cancelled_all.is_set() or bool(cancelled and cancelled())
    with Session(_remote.ssh_command_args(entry,shlex.join(args),multiplex=False),manifest,streams,budget(),cancelled) as session:
        if on_started:on_started()
        retained=set(session.ready['inventory']);completed.update(retained)
        retained_bytes=sum(manifest['chunks'][i]['bytes'] for i in retained)
        def report(force=False):
            nonlocal last_report
            now=time.monotonic()
            if progress and (force or now-last_report>=1):
                elapsed=max(now-started,.001);wire=sum(sent.values())
                progress(f'upload streams {streams} active {active}; chunks {len(completed)}/{len(manifest["chunks"])}; retained {retained_bytes} bytes; sent {wire} bytes; {wire/1_000_000/elapsed:.2f} MB/s aggregate')
                last_report=now
        with lock:report(True)
        first_error=None
        def send(item):
            nonlocal active, first_error
            if stopped(): raise RemoteLocalImageError("local_images_transfer_unknown", "Chunk scheduling cancelled; no retry was started.")
            index=item['index'];expected={'schema':3,'state':'chunk-verified','manifest_id':session.ready['manifest_id'],'index':index,'bytes':item['bytes'],'sha256':item['sha256']}
            ready={**expected,'state':'chunk-ready'}
            argv=['python3','-c',PROGRAM,'put',root,reservation,str(max(1,min(180,int(budget())))),session.ready['manifest_id'],session.ready['token'],str(index)]
            def update(count):
                with lock:sent[index]=count;report()
            with lock:active+=1
            try:
                result=local_images.stream_archive(_remote.ssh_command_args(entry,shlex.join(argv),multiplex=False),archive,
                    timeout=min(180,budget()),cancelled=stopped,ready_frame=ready,complete_frame=expected,
                    range_start=item['offset'],range_bytes=item['bytes'],on_progress=update)
                try:value=local_images._json_loads_closed(result['output_tail'].splitlines()[-1])
                except (ValueError,IndexError,UnicodeError):value=None
                if (result['returncode']!=0 or result['timed_out'] or result['cancelled'] or result['broken_pipe'] or value!=expected
                        or (not result.get('already_present') and result['sha256']!=item['sha256'])):
                    raise RemoteLocalImageError('local_images_chunk_transfer_failed',f'Chunk {index} failed; no automatic retries. Preserve verified chunks; retry same receipt with --upload-streams 1 if SSH capacity is constrained.')
                with lock:completed.add(index);report(True)
            except Exception as error:
                with lock:
                    if first_error is None: first_error=error
                cancelled_all.set()
                raise
            finally:
                with lock:active-=1
        pending_items=iter(item for item in manifest['chunks'] if item['index'] not in retained)
        with ThreadPoolExecutor(max_workers=streams) as pool:
            outstanding=set()
            def submit():
                if stopped(): return
                item=next(pending_items,None)
                if item is not None:outstanding.add(pool.submit(send,item))
            for _ in range(streams):submit()
            while outstanding:
                done,outstanding=wait(outstanding,return_when=FIRST_COMPLETED)
                for future in done:
                    try:future.result()
                    except Exception as error:
                        if first_error is None:first_error=error
                        cancelled_all.set()
                if first_error is None:
                    for _ in done:submit()
            if first_error:raise first_error
        if stopped():raise RemoteLocalImageError('local_images_transfer_unknown')
        if progress:progress(f'upload streams {streams}; chunks {len(completed)}/{len(manifest["chunks"])} verified; verifying whole bundle before Docker load')
        result=session.finalize()
        with lock:report(True)
        return {**result,'streams':streams,'chunk_count':len(manifest['chunks']),'uploaded_bytes':sum(sent.values()),
                'resumed_bytes':retained_bytes,'seconds':round(time.monotonic()-started,3)}
