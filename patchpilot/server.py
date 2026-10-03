"""Loopback-only local HTTP application. No third-party runtime dependencies."""
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from . import __version__
from .diff import parse_diff
from .evaluation import demo, evaluate
from .github import import_pr
from .reviewer import review
from .store import Store

STATIC = Path(__file__).parent / 'static'
MAX_BODY = 1_500_000

class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, port=8765, data_dir=None):
        super().__init__(('127.0.0.1', port), Handler)
        self.token = secrets.token_urlsafe(32)
        self.store = Store(Path(data_dir or os.environ.get('PATCHPILOT_DATA', '.patchpilot')) / 'reviews.sqlite3')
        self.work = threading.BoundedSemaphore(2)
        self.snapshots = {}
        self.snapshot_lock = threading.Lock()
        actual_port = self.server_address[1]
        self.hosts = {f'127.0.0.1:{actual_port}', f'localhost:{actual_port}'}
        self.origins = {f'http://{host}' for host in self.hosts}

class Handler(BaseHTTPRequestHandler):
    server_version = 'PatchPilot/1.0'
    def log_message(self, *args):
        pass # Imported source, titles, paths and prompts are never access-log data.
    def _reply(self, status, body, content_type='application/json; charset=utf-8', download=None):
        if isinstance(body, (dict,list)):
            body = json.dumps(body).encode()
        if isinstance(body,str): body=body.encode()
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if download: self.send_header('Content-Disposition',f'attachment; filename="{download}"')
        self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError): pass
    def _boundary(self, mutation=False):
        if self.headers.get('Host') not in self.server.hosts:
            self._reply(403,{'error':'Unexpected Host.'}); return False
        origin = self.headers.get('Origin')
        if origin and origin not in self.server.origins:
            self._reply(403,{'error':'Cross-origin access is refused.'}); return False
        if mutation and not secrets.compare_digest(self.headers.get('X-PatchPilot-Token',''),self.server.token):
            self._reply(403,{'error':'Missing local request token. Reload the page.'}); return False
        return True
    def do_GET(self):
        if not self._boundary(): return
        path=urlsplit(self.path).path
        assets={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
        if path in assets:
            file=STATIC/assets[path]
            mime={'html':'text/html','js':'text/javascript','css':'text/css'}[file.suffix[1:]]
            return self._reply(200,file.read_bytes(),mime+'; charset=utf-8')
        if path=='/api/config':
            return self._reply(200,dict(token=self.server.token,version=__version__,model=os.environ.get('PATCHPILOT_MODEL','qwen3:4b'),ai_endpoint='Local Ollama',ai_status='Available when configured and running',rules=5))
        if path=='/api/demo': return self._reply(200,demo())
        if path=='/api/history': return self._reply(200,self.server.store.list())
        parts=path.split('/')
        if len(parts) in (4,5) and parts[1:3]==['api','reviews']:
            result=self.server.store.get(parts[3])
            if result is None: return self._reply(404,{'error':'Review not found.'})
            if len(parts)==4: return self._reply(200,result)
            if parts[4]=='export': return self._reply(200,_markdown(result),'text/markdown; charset=utf-8','patchpilot-review.md')
        return self._reply(404,{'error':'Not found.'})
    def do_POST(self):
        if not self._boundary(True): return
        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type','').split(';')[0]!='application/json':
            return self._reply(415,{'error':'Use JSON request bodies.'})
        try:
            length=int(self.headers.get('Content-Length','-1'))
            if not 0<=length<=MAX_BODY: return self._reply(413,{'error':'Request exceeds 1.5 MB or has no length.'})
            self.connection.settimeout(10)
            payload=json.loads(self.rfile.read(length))
            if not isinstance(payload,dict): raise ValueError('JSON object required.')
        except (ValueError,TimeoutError): return self._reply(400,{'error':'Invalid JSON request.'})
        if not self.server.work.acquire(blocking=False): return self._reply(429,{'error':'Two tasks are running. Try again shortly.'})
        try:
            path=urlsplit(self.path).path
            if path=='/api/import':
                snapshot=import_pr(payload.get('url'))
                snapshot_id=secrets.token_urlsafe(24)
                with self.server.snapshot_lock:
                    now=time.monotonic()
                    self.server.snapshots={k:v for k,v in self.server.snapshots.items() if v[0]>now-900}
                    if len(self.server.snapshots)>=10:
                        self.server.snapshots.pop(next(iter(self.server.snapshots)))
                    self.server.snapshots[snapshot_id]=(now,snapshot)
                return self._reply(200,{**snapshot,'snapshot_id':snapshot_id})
            if path=='/api/review':
                title=payload.get('title','Untitled review')
                if not isinstance(title,str) or not title.strip() or len(title)>200: raise ValueError('Title must contain 1–200 characters.')
                use_ai=payload.get('use_ai',False)
                if type(use_ai) is not bool: raise ValueError('use_ai must be true or false.')
                files=payload.get('files')
                snapshot=None
                if 'snapshot_id' in payload and not isinstance(payload['snapshot_id'],str): raise ValueError('Snapshot ID must be a string.')
                if payload.get('snapshot_id'):
                    with self.server.snapshot_lock:
                        entry=self.server.snapshots.get(payload['snapshot_id'])
                    if not entry or entry[0]<time.monotonic()-900: raise ValueError('Imported snapshot expired. Import the PR again.')
                    snapshot=entry[1]
                    if payload.get('diff') != snapshot['diff'] or files != snapshot['files']:
                        raise ValueError('Imported input changed. Review it as a pasted diff or import again.')
                if files and (not isinstance(files,dict) or sum(len(v.encode()) for v in files.values() if isinstance(v,str))>1_000_000):
                    raise ValueError('Full source context exceeds 1 MB.')
                result=review(payload.get('diff'),files,use_ai)
                # Evidence is server-resolved; models cannot fabricate the displayed excerpt.
                by_path={f['path']:f for f in result['files']}
                for finding in result['findings']:
                    finding['excerpt']=next(l['text'] for l in by_path[finding['path']]['lines'] if l['kind']=='add' and l['new_line']==finding['line'])
                result['diff_hash']=__import__('hashlib').sha256(payload['diff'].encode()).hexdigest()
                if snapshot:
                    result['origin']=snapshot['origin']
                    result['warnings'].extend(snapshot['warnings'])
                saved=self.server.store.save(title.strip(),result)
                saved['files']=result['files']
                return self._reply(200,saved)
            if path=='/api/evaluate': return self._reply(200,evaluate(False))
            if path=='/api/feedback':
                self.server.store.vote(payload.get('review_id'),payload.get('finding_id'),payload.get('vote'))
                return self._reply(200,{'ok':True})
            if path=='/api/delete':
                if not isinstance(payload.get('review_id'),str): raise ValueError('Review ID required.')
                return self._reply(200,{'deleted':self.server.store.delete(payload['review_id'])})
            return self._reply(404,{'error':'Not found.'})
        except ValueError as exc: return self._reply(400,{'error':str(exc)})
        except Exception: return self._reply(500,{'error':'This task could not be completed. Check the input and try again.'})
        finally: self.server.work.release()

def _markdown(result):
    def safe(text):
        return str(text).replace('<','&lt;').replace('>','&gt;').replace('`','\\`').replace('\n',' ')
    lines=[f"# {safe(result['title'])}", '', f"Mode: {safe(result['mode'])}", f"Reviewed: {safe(result['created_at'])}", '', 'Findings are review suggestions, not proof of a bug-free change.', '']
    for item in result['findings']:
        lines.extend([f"## {safe(item['severity']).upper()}: {safe(item['title'])}",f"Location: {safe(item['path'])}:{item['line']} ({safe(item['source'])})",'',safe(item['explanation']), '', 'Suggestion: '+safe(item['suggestion']), 'Test idea: '+safe(item['test']),''])
    if not result['findings']: lines.append('No findings within the checks and context available.')
    for warning in result['warnings']: lines.append('Warning: '+safe(warning))
    return '\n'.join(lines)+'\n'

def serve(port=8765, data_dir=None):
    server=AppServer(port,data_dir)
    print(f'PatchPilot ready at http://127.0.0.1:{server.server_address[1]}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
