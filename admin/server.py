from __future__ import annotations
import base64, json, traceback, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .core import ROOT, SITE, add_resource, catalog, config, derive, infer, publish, rotate_password, secure_reset, save_catalog, unb64

ADMIN = Path(__file__).with_name('admin.html')
publish_token = ''
class Handler(BaseHTTPRequestHandler):
    def send(self, status, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        if self.path != '/': return self.send(404, {'error':'Not found'})
        raw = ADMIN.read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_POST(self):
        try:
            if int(self.headers.get('Content-Length','0')) > 60*1024*1024: raise ValueError('请求过大')
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            action = self.path
            if action == '/api/infer': return self.send(200, infer(body['filename']))
            if action == '/api/publish':
                global publish_token
                token = body.get('token', '').strip() or publish_token
                result = publish(body['remote'], token)
                publish_token = token
                return self.send(200, {'ok':True, **result})
            if action == '/api/reset':
                secure_reset(body['password'], body['newPassword']); return self.send(200, {'ok':True})
            if action == '/api/rotate':
                rotate_password(body['password'], body['newPassword']); return self.send(200, {'ok':True})
            password = body['password']
            obj, key = catalog(password)
            if action == '/api/load': return self.send(200, obj)
            if action == '/api/upload':
                if key is None: key = save_catalog(obj, password)
                items = []
                for f in body['files']:
                    items.append(add_resource(obj, key, f['name'], base64.b64decode(f['data']), f['fields']))
                save_catalog(obj, password, key)
                return self.send(200, {'items':items, 'catalog':obj})
            if action == '/api/save':
                allowed = {'resources','updates','announcements','series'}
                if set(body['catalog']) != allowed: raise ValueError('目录格式无效')
                save_catalog(body['catalog'], password, key)
                return self.send(200, {'ok':True})
            self.send(404, {'error':'Unknown action'})
        except Exception as e:
            self.send(400, {'error':str(e)})

def main():
    server = ThreadingHTTPServer(('127.0.0.1', 8765), Handler)
    print('管理员工具：http://127.0.0.1:8765')
    webbrowser.open('http://127.0.0.1:8765')
    server.serve_forever()
if __name__ == '__main__': main()
