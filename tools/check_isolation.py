import importlib.util,sys,subprocess,uuid,threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('downloader',root/'__init__.py',submodule_search_locations=[str(root)])
m=importlib.util.module_from_spec(spec);sys.modules['downloader']=m;spec.loader.exec_module(m)
from downloader.network import IMAGE,GUARD
class Handler(BaseHTTPRequestHandler):
 def do_GET(self): self.send_response(200);self.end_headers();self.wfile.write(b'reachable')
 def log_message(self,*args): pass
server=HTTPServer(('0.0.0.0',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
name='playlite-isolation-test-'+uuid.uuid4().hex[:8]
def docker(*args):
 r=subprocess.run(['docker',*args],text=True,capture_output=True,timeout=25)
 if r.returncode: raise RuntimeError(r.stderr or 'Worker connection blocked')
 return r.stdout.strip()
try:
 cid=docker('run','-d','--name',name,'--network','bridge','--cap-add','NET_ADMIN','--sysctl','net.ipv6.conf.all.disable_ipv6=1','--entrypoint','/bin/sh',IMAGE,'-c','while true; do printf "HTTP/1.0 200 OK\r\nContent-Length: 9\r\n\r\nreachable" | nc -l -p 8080; done')
 gateway=docker('inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',cid)
 args=['run','--rm','--network','container:'+cid,'--user','65534:65534','--cap-drop','ALL','--security-opt','no-new-privileges','--read-only','--entrypoint','/bin/sh',IMAGE,'-c',f'wget -q -T 2 -t 1 -O - http://{gateway}:8080']
 print('Before guard:',docker(*args))
 docker('exec',cid,'/bin/sh','-c',GUARD)
 try: docker(*args)
 except RuntimeError: print('After guard: worker HTTP blocked as expected')
 else: raise RuntimeError('Isolation test FAILED')
 print('IPv6 disabled:',docker('exec',cid,'cat','/proc/sys/net/ipv6/conf/all/disable_ipv6'))
finally:
 subprocess.run(['docker','rm','-f',name],capture_output=True);server.shutdown()
