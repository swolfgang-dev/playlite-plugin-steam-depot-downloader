"""Bounded Steam/CDN checks inside the existing verified VPN session."""
import base64
from pathlib import Path
import subprocess
import tempfile
import uuid
from .app_info import metadata_output
from .credentials import SteamSessionWallet
from .providers import prepare_depot
from .worker import Request,worker_args


class PreflightFailure(RuntimeError):
    def __init__(self,depot,manifest,message):
        super().__init__(message);self.depot=depot;self.manifest=manifest


def check_plan(network,plan,username,branch='public',progress=lambda message:None):
    if not plan or len(plan)>256:raise ValueError('CDN checks require between 1 and 256 depots.')
    wallet=SteamSessionWallet(username);saved=wallet.read()
    if not saved:raise ValueError('Authenticate with Steam before checking CDN access.')
    data=base64.b64decode(saved['data'],validate=True)
    if len(data)>4*1024*1024:raise ValueError('Saved Steam session exceeds the size limit.')
    results=[]
    with tempfile.TemporaryDirectory(prefix='playlite-cdn-check-') as directory:
        root=Path(directory);auth=root/'auth';auth.mkdir(mode=0o777);auth.chmod(0o777)
        cache=auth/'account.config';cache.write_bytes(data);cache.chmod(0o666)
        for index,(app,row,title) in enumerate(plan):
            progress(f'Checking Steam/CDN access · Depot {index+1}/{len(plan)} · {title}')
            output=root/f'output-{index}';output.mkdir(mode=0o777);output.chmod(0o777)
            pack=root/f'pack-{index}';prepare_depot(row,pack)
            args=worker_args(network,Request(app,row.id,row.manifest),output,pack=pack,username=username)
            args[args.index('--workdir')+1]='/auth'
            pos=args.index('playlite-depot-worker:test')
            args[pos:pos]=['--mount',f'type=bind,src={auth},dst=/auth']
            args+=['-remember-password','-branch',branch,'-playlite-preflight']
            # Temporary worker metadata must remain removable by the desktop user.
            args[args.index('--entrypoint')+1]='python3'
            pos=args.index('playlite-depot-worker:test')
            args[pos+1:pos+1]=['-c',"import os,sys; os.umask(0); os.execv('/tool/DepotDownloaderMod', ['/tool/DepotDownloaderMod', *sys.argv[1:]])"]
            name='playlite-cdn-check-'+uuid.uuid4().hex;args[1:1]=['--name',name]
            try:
                code,text=metadata_output(['docker',*args])
                prefix=f'PLAYLITE_PREFLIGHT {row.id} {row.manifest} '
                states=[line[len(prefix):] for line in text.splitlines() if line.startswith(prefix)]
                if code or len(states)!=1 or states[0] not in ('OK','EMPTY'):
                    raise PreflightFailure(row.id,row.manifest,f'Steam/CDN check failed for depot {row.id} ({title}). Check Steam authentication, VPN and manifest access. Update the native worker if it does not support CDN checks. Nothing was queued.')
                results.append(states[0])
            finally:subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=15)
        wallet.save({'data':base64.b64encode(cache.read_bytes()).decode()})
    return results
