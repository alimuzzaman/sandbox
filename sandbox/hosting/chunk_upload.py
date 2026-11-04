"""Fixed Linux chunk worker: one exclusive flock shared with SSH receivers.

SCM_RIGHTS duplicates the same open file description: closing/killing the
coordinator cannot release its exclusive fence while any receiver holds a copy.
No receiver releases reservations or unlocks that shared description.
"""
from __future__ import annotations

import inspect


def chunk_runtime():
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
    if not root.startswith('/') or not re.fullmatch(r'[a-f0-9]{32}',reservation):raise ValueError('chunk_protocol_invalid')
    timeout=int(timeout)
    if not 1<=timeout<=86400:raise ValueError('chunk_protocol_invalid')
    signal.alarm(timeout)
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
                os.ftruncate(fd,0);emit(chunk_frame('chunk-ready',mid,item))
                count=0;digest=hashlib.sha256()
                while count<=item['bytes']:
                    raw=sys.stdin.buffer.read(min(1048576,item['bytes']+1-count))
                    if not raw:break
                    write(fd,raw);digest.update(raw);count+=len(raw)
                os.fsync(fd)
                if count!=item['bytes'] or 'sha256:'+digest.hexdigest()!=item['sha256']:raise ValueError('chunk_hash_mismatch')
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
        raw=sys.stdin.buffer.readline(2097153)
        if len(raw)>2097152 or not raw.endswith(b'\n'):raise ValueError('chunk_manifest_invalid')
        manifest=validate(json.loads(raw,object_pairs_hook=closed));mid=identity(manifest)
        cache=root+'/chunks';os.makedirs(cache,mode=0o700,exist_ok=True)
        check=opened(cache,directory=True);os.close(check)
        names=os.listdir(cache)
        if len(names)>32 or (mid not in names and len(names)>=32):raise ValueError('partial_cache_full')
        plan=cache+'/'+mid;os.makedirs(plan,mode=0o700,exist_ok=True)
        check=opened(plan,directory=True);os.close(check)
        manifest_path=plan+'/manifest.json'
        if os.path.lexists(manifest_path):
            if load_manifest(manifest_path)!=manifest:raise ValueError('chunk_manifest_invalid')
        else:
            fd=os.open(manifest_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            try:write(fd,canonical(manifest));os.fsync(fd)
            finally:os.close(fd)
            syncdir(plan)
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
        if cached+missing>128*1024**3:raise ValueError('partial_cache_full')
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
        whole=hashlib.sha256();count=0
        for item in manifest['chunks']:
            name=leaf(plan,item,'.chunk')
            if file_hash(name,item['bytes'])!=item['sha256']:raise ValueError('chunk_hash_mismatch')
            fd=opened(name)
            try:
                while True:
                    raw=os.read(fd,1048576)
                    if not raw:break
                    whole.update(raw);count+=len(raw)
            finally:os.close(fd)
        if count!=manifest['context']['archive_bytes'] or 'sha256:'+whole.hexdigest()!=manifest['context']['bundle_digest']:raise ValueError('bundle_hash_mismatch')
        marker=os.open(root+'/load-unverified',os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:write(marker,canonical({'reservation_id':reservation}));os.fsync(marker)
        finally:os.close(marker)
        os.fchmod(fence,0o400);os.fsync(fence);syncdir(root)
        load_may_run=True
        docker=subprocess.Popen(['docker','image','load'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,pass_fds=(fence,))
        for item in manifest['chunks']:
            fd=opened(leaf(plan,item,'.chunk'))
            try:
                while True:
                    raw=os.read(fd,1048576)
                    if not raw:break
                    docker.stdin.write(raw)
            finally:os.close(fd)
        docker.stdin.close()
        if docker.wait(timeout=timeout)!=0:raise ValueError('chunk_load_unknown')
        release_storage_reservation(store,reservation)
        os.fchmod(fence,0o600);os.fsync(fence);os.unlink(root+'/load-unverified');syncdir(root)
        emit({'schema':1,'state':'loaded','bytes':count})
    finally:
        stopping.set()
        if listener is not None:listener.close()
        with condition:idle=not active
        if not load_may_run and idle:release_storage_reservation(store,reservation)
        os.close(fence)


def cleanup_chunk_plans(root):
    """Existing reconciliation holds the exclusive worker fence and no marker."""
    import os
    import re
    import stat
    import time
    if not os.path.lexists(root):return
    info=os.lstat(root)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('unsafe_chunk_cache')
    names=os.listdir(root)
    if len(names)>32:raise ValueError('partial_cache_full')
    for name in names:
        if not re.fullmatch(r'[a-f0-9]{64}',name):raise ValueError('unsafe_chunk_cache')
        plan=root+'/'+name;info=os.lstat(plan)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('unsafe_chunk_cache')
        entries=os.listdir(plan)
        if len(entries)>24577:raise ValueError('partial_cache_full')
        newest=info.st_mtime
        for entry in entries:
            if entry!='manifest.json' and not re.fullmatch(r'[0-9]{1,4}-[a-f0-9]{64}\.(?:chunk|part|lock)',entry):raise ValueError('unsafe_chunk_cache')
            info=os.lstat(plan+'/'+entry)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1:raise ValueError('unsafe_chunk_cache')
            newest=max(newest,info.st_mtime)
        if newest<time.time()-86400:
            for entry in entries:os.unlink(plan+'/'+entry)
            os.rmdir(plan)


RUNTIME_PROGRAM=inspect.getsource(chunk_runtime)+'''\ntry:
 chunk_runtime()
except Exception:
 print('{"schema":1,"state":"refused","code":"chunk_operation_failed"}',flush=True)
 raise SystemExit(74)
'''
CLEANUP_PROGRAM=inspect.getsource(cleanup_chunk_plans)
