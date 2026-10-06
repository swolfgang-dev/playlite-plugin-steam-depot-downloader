"""Verify Steam UID cannot use the main network when the tunnel is absent.

Uses disposable containers; never touches the active VPN or Steam session.
"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import uuid
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('steamcheck',root/'__init__.py',submodule_search_locations=[str(root)])
module=importlib.util.module_from_spec(spec);sys.modules['steamcheck']=module;spec.loader.exec_module(module)
from steamcheck.network import GUARD
from steamcheck.constants import IMAGE
name='playlite-steam-isolation-'+uuid.uuid4().hex
probe="import socket; s=socket.create_connection(('1.1.1.1',443),3); s.close(); print('reachable')"
try:
 subprocess.run(['docker','run','-d','--name',name,'--cap-add','NET_ADMIN','--entrypoint','/bin/sh',IMAGE,'-c','sleep 120'],capture_output=True,check=True)
 subprocess.run(['docker','exec',name,'/bin/sh','-c',GUARD],capture_output=True,check=True)
 args=['docker','run','--rm','--user','0:0','--network','container:'+name,'--cap-drop','ALL','--security-opt','no-new-privileges','--read-only','--entrypoint','python3','playlite-steam-runtime:test','-c',probe]
 baseline=subprocess.run(args,capture_output=True,timeout=15)
 if baseline.returncode:raise RuntimeError('Baseline network unavailable; isolation result would be inconclusive.')
 worker=list(args);worker[worker.index('--user')+1]='65534:65534'
 blocked=subprocess.run(worker,capture_output=True,timeout=15)
 if not blocked.returncode:raise RuntimeError('Steam UID escaped onto the main network.')
 nested=list(worker)
 index=nested.index('--entrypoint')
 nested[index:index]=['--security-opt','apparmor=unconfined','--security-opt','seccomp='+str(root/'tools/steam/seccomp.json')]
 nested[nested.index('--entrypoint')+1]='unshare'
 nested[nested.index('playlite-steam-runtime:test')+1:]=['-Ur','python3','-c',"print('namespace-ready',flush=True); "+probe]
 isolated=subprocess.run(nested,capture_output=True,timeout=15)
 if b'namespace-ready' not in isolated.stdout:raise RuntimeError('Nested namespace did not start; result would be inconclusive.')
 if not isolated.returncode:raise RuntimeError('Nested Steam namespace escaped onto the main network.')
 print('PASS: baseline reachable; Steam UID and nested user namespace blocked with no VPN tunnel.')
finally:subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=15)
