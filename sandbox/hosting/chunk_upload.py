"""Fixed Linux chunk worker: one exclusive flock shared with SSH receivers.

SCM_RIGHTS duplicates the same open file description: closing/killing the
coordinator cannot release its exclusive fence while any receiver holds a copy.
No receiver releases reservations or unlocks that shared description.
"""
from __future__ import annotations

import inspect


def chunk_runtime(status):
    import array
    import fcntl
    import hashlib
    import json
    import os
    import re
    import signal
    import socket
    import stat
    import struct
    import subprocess
    import sys
    import threading
    import time

    def private(fd, directory=False):
        info=os.fstat(fd)
        if (info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=(0o700 if directory else 0o600)
                or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink==1)):
            status['values']={'expected_mode':448 if directory else 384,'actual_mode':stat.S_IMODE(info.st_mode),'expected_uid':os.geteuid(),'actual_uid':info.st_uid,'links':info.st_nlink}
            raise ValueError('unsafe_chunk_entry')
        return info
    def opened(name, flags=os.O_RDONLY, directory=False):
        fd=os.open(name,flags|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        try: private(fd,directory)
        except BaseException: os.close(fd);raise
        return fd
    def closed(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate_chunk_key')
            result[key]=value
        return result
    def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    def identity(value):return hashlib.sha256(canonical(value)).hexdigest()
    def emit(value):print(json.dumps(value,separators=(',',':')),flush=True)
    def write(fd,data):
        view=memoryview(data)
        while view:
            n=os.write(fd,view)
            if n<=0:raise OSError('short_chunk_write')
            view=view[n:]
    def syncdir(root):
        fd=opened(root,directory=True)
        try:os.fsync(fd)
        finally:os.close(fd)
    def file_hash(name,want_size):
        fd=opened(name);digest=hashlib.sha256();count=0
        try:
            if os.fstat(fd).st_size!=want_size:return None
            while True:
                data=os.read(fd,1048576)
                if not data:break
                digest.update(data);count+=len(data)
            os.fsync(fd)
        finally:os.close(fd)
        return 'sha256:'+digest.hexdigest() if count==want_size else None
    def validate(manifest):
        if (not isinstance(manifest,dict) or set(manifest)!={'schema','context','chunk_bytes','chunks'}
                or type(manifest['schema']) is not int or manifest['schema']!=1
                or type(manifest['chunk_bytes']) is not int or not 1<=manifest['chunk_bytes']<=8*1048576):
            raise ValueError('chunk_manifest_invalid')
        ctx=manifest['context']
        if (not isinstance(ctx,dict) or set(ctx)!={'schema','project','environment','machine_identity','registered_host_digest','receipt_digest','bundle_digest','archive_bytes'}
                or type(ctx['schema']) is not int or ctx['schema']!=1 or type(ctx['archive_bytes']) is not int
                or not 0<ctx['archive_bytes']<=64*1024**3):raise ValueError('chunk_manifest_invalid')
        for key in ('receipt_digest','bundle_digest','registered_host_digest'):
            if not isinstance(ctx[key],str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',ctx[key]):raise ValueError('chunk_manifest_invalid')
        for key in ('project','environment','machine_identity'):
            if not isinstance(ctx[key],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',ctx[key]):raise ValueError('chunk_manifest_invalid')
        chunks=manifest['chunks'];size=ctx['archive_bytes'];step=manifest['chunk_bytes']
        if not isinstance(chunks,list) or not 1<=len(chunks)<=8192 or len(chunks)!=(size+step-1)//step:raise ValueError('chunk_manifest_invalid')
        for i,item in enumerate(chunks):
            if (not isinstance(item,dict) or set(item)!={'index','offset','bytes','sha256'}
                    or any(type(item.get(k)) is not int for k in ('index','offset','bytes'))
                    or item['index']!=i or item['offset']!=i*step or item['bytes']!=min(step,size-i*step)
                    or not isinstance(item['sha256'],str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',item['sha256'])):
                raise ValueError('chunk_manifest_invalid')
        return manifest
    def load_manifest(path):
        fd=opened(path)
        with os.fdopen(fd,'rb') as handle:
            if os.fstat(handle.fileno()).st_size>2097152:raise ValueError('chunk_manifest_invalid')
            return validate(json.loads(handle.read(2097153),object_pairs_hook=closed))
    def leaf(plan,item,suffix):return plan+'/'+str(item['index'])+'-'+item['sha256'][7:]+suffix
    def chunk_frame(state,mid,item):return {'schema':3,'state':state,'manifest_id':mid,'index':item['index'],'bytes':item['bytes'],'sha256':item['sha256']}
    def verify_quarantine(root):
        if os.path.lexists(root+'/load-unverified'):raise ValueError('target_busy')
    def lock_fence(root):
        fd=os.open(root+'/load-fence.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            private(fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);verify_quarantine(root)
        except BaseException:os.close(fd);raise
        return fd

    mode,root,reservation,timeout=sys.argv[1:5]
    status.update(phase='receiver_setup' if mode=='put' else 'coordinator_setup', values={}, effects={'cache':'unchanged','load':'not_started','reservation':'unknown'})
    if not root.startswith('/') or not re.fullmatch(r'[a-f0-9]{32}',reservation):raise ValueError('chunk_protocol_invalid')
    timeout=int(timeout)
    if not 1<=timeout<=86400:raise ValueError('chunk_protocol_invalid')
    def expired(_number,_frame):raise TimeoutError('chunk_deadline')
    signal.signal(signal.SIGALRM,expired);signal.alarm(timeout)
    verify_quarantine(root)
    # The root was prepared by the existing target-engine lease.
    check=opened(root,directory=True);os.close(check)
    store=os.path.dirname(root)+'/storage'
    if mode=='put':
        mid,nonce,index=sys.argv[5:]
        if not re.fullmatch(r'[a-f0-9]{64}',mid) or not re.fullmatch(r'[a-f0-9]{32}',nonce):raise ValueError('chunk_protocol_invalid')
        index=int(index)
        peer=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        peer.settimeout(min(timeout,30));peer.connect('\0sandbox-upload-'+nonce)
        peer.sendall(canonical({'manifest_id':mid,'token':nonce,'index':index,'reservation_id':reservation})+b'\n')
        data,anc,flags,_addr=peer.recvmsg(4096,socket.CMSG_SPACE(array.array('i').itemsize),socket.MSG_CMSG_CLOEXEC)
        received=[]
        for level,kind,raw in anc:
            if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                values=array.array('i');values.frombytes(raw[:len(raw)-len(raw)%values.itemsize]);received.extend(values)
        if flags&(socket.MSG_TRUNC|socket.MSG_CTRUNC) or len(received)!=1:
            for fd in received:os.close(fd)
            raise ValueError('chunk_session_unavailable')
        fence=received[0];lock=-1
        status['effects']['reservation']='retained'
        try:
            private(fence)
            if (os.fstat(fence).st_dev,os.fstat(fence).st_ino)!=(os.stat(root+'/load-fence.lock').st_dev,os.stat(root+'/load-fence.lock').st_ino):raise ValueError('chunk_session_unavailable')
            if json.loads(data,object_pairs_hook=closed)!={'ok':True}:raise ValueError('chunk_session_unavailable')
            plan=root+'/chunks/'+mid;manifest=load_manifest(plan+'/manifest.json')
            if identity(manifest)!=mid or not 0<=index<len(manifest['chunks']):raise ValueError('chunk_manifest_invalid')
            release_storage_reservation(store,reservation,verify_only=True)
            item=manifest['chunks'][index]
            lock=opened(leaf(plan,item,'.lock'),os.O_CREAT|os.O_RDWR)
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('chunk_duplicate_busy')
            ready=leaf(plan,item,'.chunk')
            if os.path.lexists(ready):
                if file_hash(ready,item['bytes'])==item['sha256']:
                    emit(chunk_frame('chunk-verified',mid,item));return
                os.unlink(ready)
            part=leaf(plan,item,'.part');fd=opened(part,os.O_CREAT|os.O_RDWR)
            try:
                status.update(phase='chunk_receive', values={'index':index,'expected_bytes':item['bytes'],'expected_sha256':item['sha256']})
                status['effects']['cache']='partial_may_exist'
                os.ftruncate(fd,0);emit(chunk_frame('chunk-ready',mid,item))
                count=0;digest=hashlib.sha256()
                while count<=item['bytes']:
                    raw=sys.stdin.buffer.read(min(1048576,item['bytes']+1-count))
                    if not raw:break
                    write(fd,raw);digest.update(raw);count+=len(raw)
                os.fsync(fd)
                if count!=item['bytes'] or 'sha256:'+digest.hexdigest()!=item['sha256']:
                    status['values'].update(actual_bytes=count,actual_sha256='sha256:'+digest.hexdigest())
                    raise ValueError('chunk_hash_mismatch')
            finally:os.close(fd)
            os.replace(part,ready);syncdir(plan);emit(chunk_frame('chunk-verified',mid,item))
        finally:
            if lock>=0:os.close(lock)
            os.close(fence);peer.close()  # Never LOCK_UN on a shared flock description.
        return
    if mode!='coordinate':raise ValueError('chunk_protocol_invalid')
    streams=int(sys.argv[5])
    if not 1<=streams<=8:raise ValueError('chunk_streams_invalid')
    fence=lock_fence(root);listener=None;active=set();condition=threading.Condition();stopping=threading.Event()
    load_may_run=False;docker=None
    try:
        release_storage_reservation(store,reservation,verify_only=True)
        status['effects']['reservation']='retained'
        status['phase']='manifest'
        raw=sys.stdin.buffer.readline(2097153)
        if len(raw)>2097152 or not raw.endswith(b'\n'):raise ValueError('chunk_manifest_invalid')
        manifest=validate(json.loads(raw,object_pairs_hook=closed));mid=identity(manifest)
        cache=root+'/chunks';os.makedirs(cache,mode=0o700,exist_ok=True)
        check=opened(cache,directory=True);os.close(check)
        names=os.listdir(cache)
        if len(names)>4 or (mid not in names and len(names)>=4):
            status['values']={'plans':len(names),'max_plans':4,'retained_bytes_limit':2*1024**3}
            raise ValueError('partial_cache_full')
        plan=cache+'/'+mid;os.makedirs(plan,mode=0o700,exist_ok=True)
        check=opened(plan,directory=True);os.close(check);syncdir(cache);syncdir(root)
        manifest_path=plan+'/manifest.json'
        # Validate file ownership/mode before replacing invalid content. The
        # incoming, digest-bound manifest is authoritative; never trust a torn
        # manifest. Known temporary files are reclaimed under the worker fence.
        status['effects']['cache']='manifest_or_chunks_may_exist'
        for name in os.listdir(plan):
            if re.fullmatch(r'\.manifest-[a-f0-9]{32}\.tmp',name):
                check=opened(plan+'/'+name);os.close(check);os.unlink(plan+'/'+name)
        valid=False
        if os.path.lexists(manifest_path):
            check=opened(manifest_path);os.close(check)
            try:valid=load_manifest(manifest_path)==manifest
            except (ValueError,UnicodeError):pass
        if not valid:
            temporary=plan+'/.manifest-'+os.urandom(16).hex()+'.tmp'
            fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            try:write(fd,canonical(manifest));os.fsync(fd)
            finally:os.close(fd)
            os.replace(temporary,manifest_path);syncdir(plan)
        os.utime(plan,None)
        inventory=[]
        for item in manifest['chunks']:
            name=leaf(plan,item,'.chunk')
            if os.path.lexists(name):
                if file_hash(name,item['bytes'])==item['sha256']:inventory.append(item['index'])
                else:os.unlink(name)
        syncdir(plan)
        # Bound total cached bytes before accepting another missing chunk.
        cached=0
        for directory in os.listdir(cache):
            if not re.fullmatch(r'[a-f0-9]{64}',directory):raise ValueError('unsafe_chunk_entry')
            base=cache+'/'+directory;check=opened(base,directory=True);os.close(check)
            entries=os.listdir(base)
            if len(entries)>24577:raise ValueError('partial_cache_full')
            for name in entries:
                fd=opened(base+'/'+name)
                try:cached+=os.fstat(fd).st_size
                finally:os.close(fd)
        missing=sum(item['bytes'] for item in manifest['chunks'] if item['index'] not in inventory)
        # Only this active, fully reserved bundle may exceed the idle 2 GiB cap.
        if cached+missing>manifest['context']['archive_bytes']+2*1024**3:
            status['values']={'cached_bytes':cached,'missing_bytes':missing,'active_bundle_bytes':manifest['context']['archive_bytes'],'retained_bytes_limit':2*1024**3}
            raise ValueError('partial_cache_full')
        nonce=os.urandom(16).hex();listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        listener.bind('\0sandbox-upload-'+nonce);listener.listen(8);listener.settimeout(.1)
        def serve(peer):
            admitted=False
            try:
                peer.settimeout(30)
                if struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]!=os.geteuid():return
                request=peer.makefile('rb',buffering=0).readline(4097)
                if len(request)>4096:return
                request=json.loads(request,object_pairs_hook=closed)
                if (not isinstance(request,dict) or set(request)!={'manifest_id','token','index','reservation_id'} or request['token']!=nonce or request['manifest_id']!=mid or request['reservation_id']!=reservation
                        or type(request['index']) is not int or not 0<=request['index']<len(manifest['chunks'])):return
                with condition:
                    if stopping.is_set() or len(active)>=streams:return
                    active.add(peer);admitted=True
                peer.sendmsg([b'{"ok":true}'],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fence]))])
                peer.settimeout(timeout)
                while peer.recv(1):pass
            except (OSError,ValueError):pass
            finally:
                peer.close()
                if admitted:
                    with condition:active.discard(peer);condition.notify_all()
        slots=threading.BoundedSemaphore(streams)
        def serve_bounded(peer):
            try:serve(peer)
            finally:slots.release()
        def accept():
            while not stopping.is_set():
                try:peer,_addr=listener.accept()
                except socket.timeout:continue
                except OSError:break
                if not slots.acquire(blocking=False):peer.close();continue
                threading.Thread(target=serve_bounded,args=(peer,),daemon=True).start()
        threading.Thread(target=accept,daemon=True).start()
        emit({'schema':1,'state':'chunk-session','manifest_id':mid,'token':nonce,'streams':streams,'inventory':inventory})
        status.update(phase='await_finalize',values={'chunks':len(manifest['chunks']),'verified_chunks':len(inventory)})
        raw=sys.stdin.buffer.readline(4097)
        if not raw:return
        request=json.loads(raw,object_pairs_hook=closed)
        if request!={'schema':1,'action':'finalize','manifest_id':mid}:raise ValueError('chunk_protocol_invalid')
        stopping.set();listener.close()
        with condition:
            deadline=time.monotonic()+5
            while active:
                left=deadline-time.monotonic()
                if left<=0:raise ValueError('chunk_writers_active')
                condition.wait(left)
        # Complete-file proof without writing a second archive: 3x admission
        # covers retained chunks plus the same Docker import/unpack as before.
        status.update(phase='bundle_verify',values={'expected_bytes':manifest['context']['archive_bytes'],'expected_sha256':manifest['context']['bundle_digest']})
        whole=hashlib.sha256();count=0
        for item in manifest['chunks']:
            name=leaf(plan,item,'.chunk')
            actual=file_hash(name,item['bytes'])
            if actual!=item['sha256']:
                status['values']={'index':item['index'],'expected_bytes':item['bytes'],'expected_sha256':item['sha256'],'actual_sha256':actual}
                raise ValueError('chunk_hash_mismatch')
            fd=opened(name)
            try:
                while True:
                    raw=os.read(fd,1048576)
                    if not raw:break
                    whole.update(raw);count+=len(raw)
            finally:os.close(fd)
        if count!=manifest['context']['archive_bytes'] or 'sha256:'+whole.hexdigest()!=manifest['context']['bundle_digest']:
            status['values'].update(actual_bytes=count,actual_sha256='sha256:'+whole.hexdigest())
            raise ValueError('bundle_hash_mismatch')
        marker=os.open(root+'/load-unverified',os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:write(marker,canonical({'reservation_id':reservation}));os.fsync(marker)
        finally:os.close(marker)
        os.fchmod(fence,0o400);os.fsync(fence);syncdir(root)
        load_may_run=True
        status.update(phase='docker_load',values={})
        status['effects'].update(load='may_have_run',quarantine=True)
        docker=subprocess.Popen(['docker','image','load'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,pass_fds=(fence,))
        try:
            for item in manifest['chunks']:
                fd=opened(leaf(plan,item,'.chunk'))
                try:
                    while True:
                        raw=os.read(fd,1048576)
                        if not raw:break
                        docker.stdin.write(raw)
                finally:os.close(fd)
            docker.stdin.close()
        except BrokenPipeError:
            try:docker.stdin.close()
            except BrokenPipeError:pass
            status['values']={'docker_exit':docker.wait(timeout=timeout)}
            raise ValueError('chunk_load_unknown') from None
        status['values']={'docker_exit':docker.wait(timeout=timeout)}
        if status['values']['docker_exit']!=0:raise ValueError('chunk_load_unknown')
        status['effects']['load']='completed'
        status['phase']='cache_cleanup'
        cleanup_chunk_plans(cache,finish_manifest=mid)
        status['effects']['cache']='removed'
        status['phase']='reservation_release'
        release_storage_reservation(store,reservation)
        status['effects']['reservation']='released'
        os.fchmod(fence,0o600);os.fsync(fence);os.unlink(root+'/load-unverified');syncdir(root)
        emit({'schema':1,'state':'loaded','bytes':count})
    finally:
        stopping.set()
        if listener is not None:listener.close()
        with condition:idle=not active
        if not load_may_run and idle:
            # Preserve the primary failure, including its phase/values. Cleanup
            # may fail independently; retain the reservation if cache GC fails.
            try:
                cleanup_chunk_plans(root+'/chunks',keep_manifest=locals().get('mid'))
                release_storage_reservation(store,reservation)
                status['effects']['reservation']='released'
            except Exception:status['effects']['cleanup']='pending'
        os.close(fence)


def cleanup_chunk_plans(root, *, keep_receipt=None, keep_manifest=None, evict_other=False, finish_manifest=None):
    """Caller holds EX worker fence and proved no quarantine (success excepted).

    Scan at most the legacy 32 plans, 24578 entries each. New sessions permit
    four plans; idle retention is at most 2 GiB. Before unrelated admission all
    foreign idle plans are removed, regardless of TTL. Never called unfenced.
    """
    import hashlib
    import json
    import os
    import re
    import stat
    import time
    if not os.path.lexists(root):return set()
    def directory(name):
        info=os.lstat(name)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('unsafe_chunk_cache',{'expected_mode':448,'actual_mode':stat.S_IMODE(info.st_mode),'expected_uid':os.geteuid(),'actual_uid':info.st_uid})
        return info
    directory(root);names=os.listdir(root)
    if len(names)>32:raise ValueError('partial_cache_full',{'plans':len(names),'max_plans':32})
    plans=[]
    for name in names:
        if not re.fullmatch(r'[a-f0-9]{64}',name):raise ValueError('unsafe_chunk_cache')
        plan=root+'/'+name;info=directory(plan);entries=os.listdir(plan)
        if len(entries)>24578:raise ValueError('partial_cache_full',{'entries':len(entries),'max_entries':24578})
        newest=info.st_mtime;size=0
        for entry in entries:
            if entry!='manifest.json' and not re.fullmatch(r'(?:[0-9]{1,4}-[a-f0-9]{64}\.(?:chunk|part|lock)|\.manifest-[a-f0-9]{32}\.tmp)',entry):raise ValueError('unsafe_chunk_cache')
            info=os.lstat(plan+'/'+entry)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1:raise ValueError('unsafe_chunk_cache',{'expected_mode':384,'actual_mode':stat.S_IMODE(info.st_mode),'expected_uid':os.geteuid(),'actual_uid':info.st_uid,'links':info.st_nlink})
            newest=max(newest,info.st_mtime);size+=info.st_size
        keep=name==keep_manifest;project=None
        if 'manifest.json' in entries:
            fd=os.open(plan+'/manifest.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            try:
                info=os.fstat(fd)
                if info.st_size<=2097152:
                    try:
                        meta=json.loads(os.read(fd,2097153));ctx=meta.get('context',{})
                        if hashlib.sha256(json.dumps(meta,sort_keys=True,separators=(',',':')).encode()).hexdigest()==name:
                            candidate=ctx.get('project')
                            if isinstance(candidate,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',candidate):project=candidate
                            keep=keep or (keep_receipt is not None and ctx.get('receipt_digest')==keep_receipt)
                    except (ValueError,UnicodeError,AttributeError,TypeError):pass
            finally:os.close(fd)
        plans.append((not keep,newest,name,entries,size,project))
    retained=0;count=0;removed_projects=set()
    for foreign,newest,name,entries,size,project in sorted(plans):
        metadata=os.lstat(root+'/'+name+'/manifest.json').st_size if 'manifest.json' in entries else 0
        remove=(retained+metadata>2*1024**3 or name==finish_manifest or newest<time.time()-86400 or
                (evict_other and foreign) or count>=4)
        if remove:
            for entry in entries:os.unlink(root+'/'+name+'/'+entry)
            os.rmdir(root+'/'+name)
            if project is not None:removed_projects.add(project)
        else:
            # Retain independent verified chunks up to the idle cap, rather
            # than throwing away every chunk of a large interrupted bundle.
            used=0
            for entry in sorted(entries,key=lambda item:(item!='manifest.json',not item.endswith('.chunk'),item)):
                current=os.lstat(root+'/'+name+'/'+entry).st_size
                if entry!='manifest.json' and retained+used+current>2*1024**3:os.unlink(root+'/'+name+'/'+entry)
                else:used+=current
            retained+=used;count+=1
            fd=os.open(root+'/'+name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(fd)
            finally:os.close(fd)
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
    return removed_projects


CLEANUP_PROGRAM=inspect.getsource(cleanup_chunk_plans)
RUNTIME_PROGRAM=CLEANUP_PROGRAM+inspect.getsource(chunk_runtime)+"""
status={'phase':'worker_start','values':{},'effects':{'cache':'unknown','load':'unknown','reservation':'unknown'}}
try:
 chunk_runtime(status)
except Exception as error:
 import errno,json,subprocess
 known={'unsafe_chunk_entry','unsafe_chunk_cache','duplicate_chunk_key','chunk_manifest_invalid','chunk_protocol_invalid','chunk_streams_invalid','chunk_session_unavailable','chunk_duplicate_busy','chunk_hash_mismatch','bundle_hash_mismatch','target_busy','partial_cache_full','chunk_writers_active','chunk_load_unknown'}
 reservation={'operation reservation missing':'reservation_missing','reservation ownership differs':'reservation_owner_mismatch','unsafe reservation store':'reservation_store_unsafe','unsafe reservation lock':'reservation_store_unsafe','unsafe reservation ledger':'reservation_ledger_unsafe','invalid reservation ledger':'reservation_ledger_invalid','reservation lock busy':'reservation_lock_busy'}
 code=error.args[0] if error.args and isinstance(error.args[0],str) and error.args[0] in known else reservation.get(error.args[0] if error.args and isinstance(error.args[0],str) else '')
 if code is not None and len(error.args)>1 and isinstance(error.args[1],dict):status['values']=error.args[1]
 if code is None:
  number=getattr(error,'errno',None)
  code='chunk_no_space' if number==errno.ENOSPC else 'chunk_permission_denied' if number==errno.EACCES else 'chunk_missing' if number==errno.ENOENT else 'target_busy' if number in (errno.EAGAIN,errno.EWOULDBLOCK) else 'chunk_timeout' if isinstance(error,(TimeoutError,subprocess.TimeoutExpired)) else 'chunk_operation_failed'
  if isinstance(number,int):status['values']={'errno':number}
  if code=='chunk_timeout':status['values']={'timeout_seconds':timeout if 'timeout' in globals() else int(__import__('sys').argv[4])}
 print(json.dumps({'schema':1,'state':'refused','code':code,**status},separators=(',',':')),flush=True)
 raise SystemExit(74)
"""
