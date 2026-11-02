from __future__ import annotations
import base64, datetime as dt, hashlib, json, os, re, secrets, subprocess, tempfile, uuid
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'docs'
DATA = SITE / 'data'
ITERATIONS = 600_000

def b64(data): return base64.b64encode(data).decode('ascii')
def unb64(data): return base64.b64decode(data)
def derive(password: str, salt: bytes) -> bytes:
    if len(password) < 8: raise ValueError('统一密码至少需要 8 个字符')
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS, 32)
def seal(data: bytes, key: bytes) -> bytes:
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, data, None)
def open_sealed(data: bytes, key: bytes) -> bytes:
    return AESGCM(key).decrypt(data[:12], data[12:], None)
def config():
    p = DATA / 'config.json'
    return json.loads(p.read_text()) if p.exists() else None
def unwrap_key(password, cfg):
    wrapping_key = derive(password, unb64(cfg['salt']))
    return open_sealed(unb64(cfg['wrappedKey']), wrapping_key)

def catalog(password):
    cfg = config()
    if not cfg: return {'resources': [], 'updates': [], 'announcements': [], 'series': []}, None
    file_key = unwrap_key(password, cfg)
    obj = json.loads(open_sealed((DATA/'catalog.bin').read_bytes(), file_key))
    obj.setdefault('series', [])
    return obj, file_key

def save_catalog(obj, password, key=None):
    DATA.mkdir(parents=True, exist_ok=True)
    cfg = config()
    if not cfg:
        salt = secrets.token_bytes(16)
        key = secrets.token_bytes(32)
        cfg = {'format': 2, 'kdf': 'PBKDF2-SHA256', 'iterations': ITERATIONS,
               'salt': b64(salt), 'wrappedKey': b64(seal(key, derive(password, salt)))}
        (DATA/'config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    key = key or unwrap_key(password, cfg)
    (DATA/'catalog.bin').write_bytes(seal(json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode(), key))
    return key

def infer(name):
    stem = Path(name).stem
    m = re.search(r'(?i)\b(BE\s*\d+)\s*[-_ ]*U(?:nit)?\s*(\d+)\b', stem)
    return {'book': m.group(1).upper().replace(' ', '') if m else '', 'unit': int(m.group(2)) if m else None,
            'title': stem, 'lessonType': re.sub(r'^[\s_-]+', '', stem[m.end():]) if m else ''}

def convert_docx(src: Path, dest: Path):
    script = '''on run argv
set inputPath to item 1 of argv
set outputPath to item 2 of argv
tell application "Microsoft Word"
 activate
 open (POSIX file inputPath)
 set docRef to active document
 save as docRef file name outputPath file format format PDF
 close docRef saving no
end tell
end run'''
    p = subprocess.run(['osascript', '-e', script, str(src), str(dest)], capture_output=True, text=True, timeout=180)
    if p.returncode or not dest.exists(): raise RuntimeError('Word 转 PDF 失败：' + (p.stderr.strip() or p.stdout.strip()))

def add_resource(obj, key, filename, raw, fields, converted=False):
    if len(raw) > 40*1024*1024: raise ValueError('单个文件不能超过 40 MB')
    original = Path(filename).name
    ext = Path(original).suffix.lower()
    if ext not in ('.pdf', '.docx', '.pptx', '.xlsx'): raise ValueError('支持 PDF、DOCX、PPTX、XLSX')
    rid = str(fields.get('id') or uuid.uuid4())
    old = next((r for r in obj['resources'] if r['id'] == rid), None)
    version = (old['version'] + 1) if old else 1
    (DATA/'blobs').mkdir(exist_ok=True)
    original_blob = f'{rid}-v{version}-original.bin'
    (DATA/'blobs'/original_blob).write_bytes(seal(raw, key))
    pdf = raw if ext == '.pdf' else None
    if ext == '.docx':
        with tempfile.TemporaryDirectory() as td:
            src, out = Path(td)/'input.docx', Path(td)/'output.pdf'
            src.write_bytes(raw); convert_docx(src, out); pdf = out.read_bytes()
    if pdf is not None and not pdf.startswith(b'%PDF-'): raise ValueError('PDF 文件无效')
    blob = f'{rid}-v{version}-view.bin' if pdf is not None else None
    if pdf is not None: (DATA/'blobs'/blob).write_bytes(seal(pdf, key))
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    item = {'id': rid, 'title': str(fields.get('title') or Path(original).stem).strip(),
            'book': str(fields.get('book') or '').strip(), 'unit': fields.get('unit'),
            'lessonType': str(fields.get('lessonType') or '').strip(), 'category': str(fields.get('category') or '教案').strip(),
            'download': bool(fields.get('download', True)), 'filename': Path(original).stem + '.pdf',
            'originalFilename': original, 'originalBlob': original_blob, 'viewable': pdf is not None,
            'version': version, 'updatedAt': now, 'blob': blob, 'size': len(raw), 'status': 'published',
            'history': (old.get('history', []) + [{k: old.get(k) for k in ('version','blob','originalBlob','updatedAt','filename','originalFilename')}]) if old else []}
    if old: obj['resources'].remove(old)
    obj['resources'].append(item)
    note = str(fields.get('note') or ('资源已更新' if old else '新增资源')).strip()
    obj['updates'].insert(0, {'id': str(uuid.uuid4()), 'resourceId': rid, 'title': item['title'], 'text': note,
                              'kind': 'updated' if old else 'created', 'createdAt': now, 'visible': bool(fields.get('showUpdate', True))})
    return item

def publish(remote):
    if not re.fullmatch(r'https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?', remote): raise ValueError('仅接受 GitHub HTTPS 仓库地址')
    subprocess.run(['git','init','-b','main'], cwd=ROOT, check=True, capture_output=True)
    remotes = subprocess.run(['git','remote'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    subprocess.run(['git','remote','set-url','origin',remote] if 'origin' in remotes else ['git','remote','add','origin',remote], cwd=ROOT, check=True)
    subprocess.run(['git','fetch','origin','main'], cwd=ROOT, check=True, timeout=120)
    subprocess.run(['git','reset','--mixed','origin/main'], cwd=ROOT, check=True)
    subprocess.run(['git','add','docs'], cwd=ROOT, check=True, capture_output=True)
    subprocess.run(['git','-c','user.name=Beavers Education','-c','user.email=beavers@users.noreply.github.com','commit','-m','Publish teaching resources'], cwd=ROOT, check=True, capture_output=True) if subprocess.run(['git','diff','--cached','--quiet'], cwd=ROOT).returncode else None
    subprocess.run(['git','push','-u','origin','main'], cwd=ROOT, check=True, timeout=120)


def rotate_password(old_password, new_password):
    cfg = config()
    if not cfg: raise ValueError('资源库尚未初始化')
    file_key = unwrap_key(old_password, cfg)
    new_salt = secrets.token_bytes(16)
    new_cfg = {'format': 2, 'kdf': 'PBKDF2-SHA256', 'iterations': ITERATIONS,
               'salt': b64(new_salt), 'wrappedKey': b64(seal(file_key, derive(new_password, new_salt)))}
    (DATA/'config.json').write_text(json.dumps(new_cfg, ensure_ascii=False, indent=2))

def secure_reset(old_password, new_password):
    obj, old_key = catalog(old_password)
    if old_key is None: raise ValueError('资源库尚未初始化')
    new_key = secrets.token_bytes(32)
    blob_dir = DATA/'blobs'
    if blob_dir.exists():
        for path in blob_dir.glob('*.bin'):
            path.write_bytes(seal(open_sealed(path.read_bytes(), old_key), new_key))
    (DATA/'catalog.bin').write_bytes(seal(json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode(), new_key))
    salt = secrets.token_bytes(16)
    cfg = {'format': 2, 'kdf': 'PBKDF2-SHA256', 'iterations': ITERATIONS,
           'salt': b64(salt), 'wrappedKey': b64(seal(new_key, derive(new_password, salt)))}
    (DATA/'config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
