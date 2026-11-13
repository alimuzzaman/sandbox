"""Fixed stdin-only runtime publication program for the selected Linux owner.

No payload bytes, encodings or digests enter argv or its metadata-only reply.
The caller owns execution authority, original deadlines and unknown-effect fences.
"""

PROGRAM = r'''
import fcntl,json,os,pathlib,secrets,signal,stat,sys

LIMIT=16*1024*1024
WALK=getattr(os,'O_PATH',os.O_RDONLY)|os.O_DIRECTORY|os.O_NOFOLLOW
parent_fd=lock_fd=stage_fd=-1
stage_name=None
stage_identity=None
replacing=False
published=False

def refuse():
 raise RuntimeError('runtime_file_publication_refused')

def interrupted(signum,frame):
 raise InterruptedError('runtime_file_publication_interrupted')

def signature(s):
 return (s.st_dev,s.st_ino,s.st_uid,s.st_gid,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def safe_directory(s):
 if not stat.S_ISDIR(s.st_mode) or s.st_uid not in (0,os.geteuid()):refuse()
 if stat.S_IMODE(s.st_mode)&0o022 and not (s.st_uid==0 and s.st_mode&stat.S_ISVTX):refuse()

def check_parent():
 observed=os.stat(path.parent,follow_symlinks=False)
 safe_directory(observed)
 if (observed.st_dev,observed.st_ino)!=(parent_identity.st_dev,parent_identity.st_ino):refuse()

def current(name):
 try:fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent_fd)
 except FileNotFoundError:return None,None
 try:
  s=os.fstat(fd)
  if (not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid() or s.st_nlink!=1
      or stat.S_IMODE(s.st_mode)&0o022 or (mode==0o600 and stat.S_IMODE(s.st_mode)&0o077)):refuse()
  chunks=[];total=0
  while True:
   chunk=os.read(fd,min(65536,LIMIT+1-total))
   if not chunk:break
   total+=len(chunk)
   if total>LIMIT:refuse()
   chunks.append(chunk)
  if signature(os.fstat(fd))!=signature(s) or signature(os.stat(name,dir_fd=parent_fd,follow_symlinks=False))!=signature(s):refuse()
  return signature(s),b''.join(chunks)
 finally:os.close(fd)

try:
 signal.signal(signal.SIGTERM,interrupted)
 signal.signal(signal.SIGINT,interrupted)
 os.umask(0o077)
 path=pathlib.Path(sys.argv[1]);mode=int(sys.argv[2],8)
 if (path.anchor!='/' or str(path)!=os.path.normpath(str(path))
     or any(part in ('.','..') for part in path.parts) or not path.name
     or mode not in (0o600,0o644)):refuse()
 parent_fd=os.open('/',WALK)
 safe_directory(os.fstat(parent_fd))
 for part in path.parent.parts[1:]:
  try:child=os.open(part,WALK,dir_fd=parent_fd)
  except FileNotFoundError:
   os.mkdir(part,0o700,dir_fd=parent_fd)
   sync_fd=os.open('.',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent_fd)
   try:os.fsync(sync_fd)
   finally:os.close(sync_fd)
   child=os.open(part,WALK,dir_fd=parent_fd)
  safe_directory(os.fstat(child));os.close(parent_fd);parent_fd=child
 # Ancestors need search permission, not directory-list permission. Only the
 # destination parent needs a readable descriptor for the publication fsync.
 child=os.open('.',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent_fd)
 os.close(parent_fd);parent_fd=child
 parent_identity=os.fstat(parent_fd)
 lock_name='.'+path.name+'.sandbox-publish.lock'
 lock_fd=os.open(lock_name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=parent_fd)
 s=os.fstat(lock_fd)
 if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid() or stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1:refuse()
 fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 before,old=current(path.name)
 header=sys.stdin.buffer.read(8)
 if len(header)!=8:refuse()
 expected=int.from_bytes(header,'big')
 if expected>LIMIT:refuse()
 payload=sys.stdin.buffer.read(expected+1)
 if len(payload)!=expected:refuse()
 if old==payload and before is not None and stat.S_IMODE(before[4])==mode:
  latest,latest_payload=current(path.name)
  if latest!=before or latest_payload!=payload:refuse()
  check_parent()
  print(json.dumps({'ok':True,'status':'already_current'}));sys.exit(0)
 stage_name='.'+path.name+'.sandbox-stage-'+secrets.token_hex(16)
 stage_fd=os.open(stage_name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=parent_fd)
 stage_identity=os.fstat(stage_fd)
 if not stat.S_ISREG(stage_identity.st_mode) or stage_identity.st_uid!=os.geteuid() or stage_identity.st_nlink!=1 or stat.S_IMODE(stage_identity.st_mode)!=0o600:refuse()
 view=memoryview(payload)
 while view:
  written=os.write(stage_fd,view)
  if written<=0:refuse()
  view=view[written:]
 os.fchmod(stage_fd,mode);os.fsync(stage_fd)
 latest,_=current(path.name)
 check_parent()
 if latest!=before:refuse()
 stage_now=os.stat(stage_name,dir_fd=parent_fd,follow_symlinks=False)
 if (stage_now.st_dev,stage_now.st_ino)!=(stage_identity.st_dev,stage_identity.st_ino):refuse()
 replacing=True
 os.replace(stage_name,path.name,src_dir_fd=parent_fd,dst_dir_fd=parent_fd)
 stage_name=None;published=True
 os.fsync(parent_fd)
 print(json.dumps({'ok':True,'status':'published'}))
except SystemExit:
 raise
except BaseException:
 print(json.dumps({'ok':False,'status':'publication_unknown' if replacing or published else 'not_published'}))
 sys.exit(1)
finally:
 if stage_fd>=0:
  if stage_identity is None:
   try:stage_identity=os.fstat(stage_fd)
   except OSError:pass
  os.close(stage_fd)
 if stage_name is not None and parent_fd>=0 and stage_identity is not None:
  try:
   s=os.stat(stage_name,dir_fd=parent_fd,follow_symlinks=False)
   if (s.st_dev,s.st_ino)==(stage_identity.st_dev,stage_identity.st_ino):os.unlink(stage_name,dir_fd=parent_fd)
  except OSError:pass
 if lock_fd>=0:os.close(lock_fd)
 if parent_fd>=0:os.close(parent_fd)
'''
