"""Upload connection budget and failure contract, without a host or sshd."""
from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from sandbox.transports.chunk_connections import UploadConnections, ConnectionFailure


class ChunkConnectionTests(unittest.TestCase):
    def pool(self,streams=8):
        with patch('sandbox.transports.chunk_connections._remote.ssh_command_args',return_value=['ssh','-oControlMaster=no','fake-host','']):
            return UploadConnections({},streams,lambda:30,lambda:False)

    def test_serial_authentication_reuses_eight_connections_for_many_chunks(self):
        import time
        pool=self.pool();auth=0;peak=0;starts=[];guard=threading.Lock()
        def operation(argv):
            nonlocal auth,peak
            if '-M' in argv:
                with guard:auth+=1;peak=max(peak,auth);starts.append(time.monotonic())
                time.sleep(.005)
                with guard:auth-=1
            return 0,'none'
        with patch.object(pool,'operation',side_effect=operation),patch('sandbox.transports.chunk_connections.subprocess.run'):
            with pool:
                def send(index):
                    with pool.channel('chunk '+str(index)) as argv:
                        self.assertNotIn('-M',argv)
                        self.assertIn('-oProxyCommand=false',argv)
                        self.assertEqual(argv.count('-S'),1)
                        time.sleep(.01)
                        return argv[argv.index('-S')+1]
                with ThreadPoolExecutor(max_workers=8) as executor:paths=list(executor.map(send,range(41)))
        self.assertEqual(peak,1);self.assertEqual(auth,0)
        self.assertEqual(len(starts),8)
        self.assertTrue(all(b-a>=.24 for a,b in zip(starts,starts[1:])))
        self.assertEqual(len(set(paths)),8)
        self.assertFalse(Path(pool.directory.name).exists())

    def test_refused_setup_backs_off_but_never_retries_payload_or_credentials(self):
        for cause,attempts,delays in [('connection_refused',3,[.25,1,2]),('authentication_denied',1,[.25])]:
            pool=self.pool(1)
            with patch.object(pool,'operation',return_value=(255,cause)) as operation,patch('sandbox.transports.chunk_connections.time.sleep') as sleep:
                with self.assertRaises(ConnectionFailure) as error:
                    with pool:self.fail('refused authentication accepted')
            self.assertEqual(operation.call_count,attempts)
            self.assertEqual(error.exception.cause,cause)
            self.assertEqual(error.exception.attempts,attempts)
            self.assertEqual([call.args[0] for call in sleep.call_args_list],delays)

    def test_master_and_channel_settings_disable_hidden_auth_fallback(self):
        pool=self.pool(1)
        master=pool.argv('/private/control','-M','-N','-f')
        self.assertLess(master.index('-oControlMaster=yes'),master.index('-oControlMaster=no'))
        self.assertIn('-oControlPersist=60',master)
        self.assertIn('-oConnectionAttempts=1',master)
        self.assertIn('-oBatchMode=yes',master)
        for argv in (pool.argv('/private/control',command='chunk'),pool.argv('/private/control','-O','check')):
            self.assertIn('-oProxyCommand=false',argv)
            self.assertNotIn('-M',argv)

    def test_bad_remote_failure_never_echoes_untrusted_fields(self):
        from sandbox.transports.chunk_failures import worker_failure
        for value in ({'state':'refused','code':['secret']}, {'schema':1,'state':'refused','code':[], 'phase':'manifest','values':{},'effects':{}},
                      {'schema':1,'state':'refused','code':'chunk_hash_mismatch','phase':'manifest','values':{'index':'secret'},'effects':{'cache':'unknown','load':'not_started','reservation':'retained'}}):
            self.assertIsNone(worker_failure(value))

    def fake_ssh(self,directory):
        import sys
        target=Path(directory)/'ssh';events=Path(directory)/'events'
        target.write_text('#!'+sys.executable+'\n'+r'''
import fcntl,json,os,shlex,socket,sys,time
EVENTS=EVENT_PATH
args=sys.argv[1:];path=args[args.index('-S')+1]
def event(kind):
 with open(EVENTS,'a') as log:
  fcntl.flock(log,fcntl.LOCK_EX);log.write(json.dumps({'kind':kind,'time':time.monotonic(),'pid':os.getpid(),'target':args[-1]})+'\n');log.flush()
if '-M' in args:
 event('auth_start');time.sleep(1.2 if args[-1]=='slow-host' else .02)
 listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);listener.bind(path);listener.listen(10)
 event('auth_ready')
 if os.fork():raise SystemExit(0)
 os.setsid();listener.settimeout(.6) # accelerated fake of ControlPersist=60
 try:
  while True:
   try:peer,_=listener.accept()
   except socket.timeout:event('idle_exit');break
   message=peer.recv(16);peer.sendall(b'ok');peer.close()
   if message==b'exit':event('explicit_exit');break
   if message==b'channel':event('channel')
 finally:
  listener.close()
  try:os.unlink(path)
  except FileNotFoundError:pass
 os._exit(0)
client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
try:client.connect(path)
except OSError:
 event('mux_refused');raise SystemExit(255)
if '-O' in args:message=args[args.index('-O')+1].encode()
else:message=b'channel'
client.sendall(message);assert client.recv(16)==b'ok';client.close()
if message in (b'check',b'exit'):raise SystemExit(0)
command=shlex.split(args[-1]);os.execv(command[0],command)
'''.replace('EVENT_PATH',repr(str(events))))
        target.chmod(0o700)
        return target,events

    def test_fake_ssh_processes_reuse_masters_and_master_loss_never_reauthenticates(self):
        import json,shlex,subprocess,sys
        from tests.subprocess_support import synthetic_environment
        actual_run=subprocess.run
        def isolated(*args,**kwargs):kwargs['env']=synthetic_environment();return actual_run(*args,**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            ssh,events=self.fake_ssh(directory)
            with patch('sandbox.transports.chunk_connections._remote.ssh_command_args',return_value=[str(ssh),'fake-host','']),patch('sandbox.transports.chunk_connections.subprocess.run',side_effect=isolated):
                with UploadConnections({},2,lambda:20,lambda:False) as pool:
                    for i in range(7):
                        with pool.channel(shlex.join([sys.executable,'-c','print(7)'])) as argv:
                            result=actual_run(argv,capture_output=True,text=True,env=synthetic_environment(),timeout=3)
                            self.assertEqual(result.stdout,'7\n');self.assertEqual(result.returncode,0)
                    for path in pool.paths:pool.operation(pool.argv(path,'-O','exit'))
                    with pool.channel(shlex.join([sys.executable,'-c','print(8)'])) as argv:
                        refused=actual_run(argv,capture_output=True,env=synthetic_environment(),timeout=3)
                        self.assertEqual(refused.returncode,255)
            rows=[json.loads(line) for line in events.read_text().splitlines()]
            self.assertEqual(sum(row['kind']=='auth_start' for row in rows),2)
            self.assertEqual(sum(row['kind']=='channel' for row in rows),7)
            active=peak=0
            for row in rows:
                if row['kind']=='auth_start':active+=1;peak=max(active,peak)
                if row['kind']=='auth_ready':active-=1
            self.assertEqual(peak,1);self.assertEqual(active,0)

    def test_controller_death_reaps_fake_persistent_masters_after_idle_bound(self):
        import json,signal,subprocess,sys,time
        from tests.subprocess_support import synthetic_environment
        with tempfile.TemporaryDirectory() as directory:
            ssh,events=self.fake_ssh(directory)
            harness='''
import json,sys,time
from unittest.mock import patch
from sandbox.transports.chunk_connections import UploadConnections
with patch('sandbox.transports.chunk_connections._remote.ssh_command_args',return_value=[sys.argv[1],'fake-host','']):
 with UploadConnections({},2,lambda:20,lambda:False) as pool:
  print(json.dumps({'paths':pool.paths,'directory':pool.directory.name}),flush=True)
  time.sleep(20)
'''
            controller=subprocess.Popen([sys.executable,'-c',harness,str(ssh)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=synthetic_environment())
            try:
                ready=json.loads(controller.stdout.readline())
                controller.kill();controller.wait(timeout=3)
                self.assertEqual(controller.returncode,-signal.SIGKILL)
                deadline=time.monotonic()+3
                while any(Path(path).exists() for path in ready['paths']) and time.monotonic()<deadline:time.sleep(.02)
                self.assertFalse(any(Path(path).exists() for path in ready['paths']))
                rows=[json.loads(line) for line in events.read_text().splitlines()]
                self.assertEqual(sum(row['kind']=='idle_exit' for row in rows),2)
                Path(ready['directory']).rmdir()
            finally:
                if controller.poll() is None:controller.kill();controller.wait(timeout=3)
                controller.stdout.close();controller.stderr.close()

    def test_two_unrelated_slow_logins_plus_upload_have_at_most_three_unauthenticated(self):
        import json,subprocess,time
        from tests.subprocess_support import synthetic_environment
        actual_run=subprocess.run
        def isolated(*args,**kwargs):kwargs['env']=synthetic_environment();return actual_run(*args,**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            ssh,events=self.fake_ssh(directory)
            paths=[str(Path(directory)/str(index)) for index in (0,1)]
            with ThreadPoolExecutor(max_workers=2) as executor:
                others=[executor.submit(actual_run,[str(ssh),'-S',path,'-M','-N','-f','slow-host'],env=synthetic_environment(),timeout=3,check=True) for path in paths]
                deadline=time.monotonic()+2
                while (not events.exists() or events.read_text().count('auth_start')<2) and time.monotonic()<deadline:time.sleep(.01)
                with patch('sandbox.transports.chunk_connections._remote.ssh_command_args',return_value=[str(ssh),'fake-host','']),patch('sandbox.transports.chunk_connections.subprocess.run',side_effect=isolated):
                    with UploadConnections({},2,lambda:20,lambda:False):pass
                for future in others:future.result()
            for path in paths:actual_run([str(ssh),'-S',path,'-O','exit','slow-host'],env=synthetic_environment(),timeout=3,check=True)
            rows=[json.loads(line) for line in events.read_text().splitlines()]
            active=peak=own=own_peak=0
            for row in rows:
                delta=1 if row['kind']=='auth_start' else -1 if row['kind']=='auth_ready' else 0
                active+=delta;peak=max(peak,active)
                if row['target']=='fake-host':own+=delta;own_peak=max(own_peak,own)
            self.assertEqual(peak,3);self.assertEqual(own_peak,1);self.assertEqual(active,0)

    def test_ambiguous_background_start_with_socket_never_reauthenticates(self):
        import subprocess
        pool=self.pool(1)
        def ambiguous(argv):
            Path(argv[argv.index('-S')+1]).touch()
            raise subprocess.TimeoutExpired('ssh',20)
        with patch.object(pool,'operation',side_effect=ambiguous) as operation,patch('sandbox.transports.chunk_connections.subprocess.run'):
            with self.assertRaises(ConnectionFailure) as caught:
                with pool:self.fail('ambiguous startup reused')
        self.assertEqual(operation.call_count,1)
        self.assertEqual(caught.exception.cause,'master_state_unknown')

    def test_real_ssh_mux_fallback_opens_zero_connections_even_with_eight_clients(self):
        import shutil,socket,subprocess
        from tests.subprocess_support import synthetic_environment
        ssh=shutil.which('ssh',path='/usr/bin:/bin')
        if ssh is None:self.skipTest('Local OpenSSH client unavailable')
        with tempfile.TemporaryDirectory() as directory,socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen(16);listener.settimeout(.05)
            with patch('sandbox.transports.chunk_connections._remote.ssh_command_args',return_value=[ssh,'-F','/dev/null','-oIdentityAgent=none','-oConnectTimeout=1','-p',str(listener.getsockname()[1]),'127.0.0.1','']):
                pool=UploadConnections({},8,lambda:10,lambda:False)
            def client(index):
                argv=pool.argv(str(Path(directory)/str(index)),command='true')
                return subprocess.run(argv,capture_output=True,env=synthetic_environment({'HOME':directory}),timeout=3).returncode
            with ThreadPoolExecutor(max_workers=8) as executor:self.assertEqual(list(executor.map(client,range(8))),[255]*8)
            with self.assertRaises(socket.timeout):listener.accept()
