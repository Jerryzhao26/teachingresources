from __future__ import annotations
import base64, datetime as dt, hashlib, json, os, re, secrets, ssl, subprocess, tempfile, uuid
import urllib.error, urllib.request
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

def normalize_lesson_type(value):
    s = str(value or '')
    if '每周两次' in s or '一周两次' in s: return '一周两次'
    if '每周一次' in s or '一周一次' in s: return '一周一次'
    if '每天' in s: return '每天上课'
    if '教材' in s: return '教材'
    return ''

def infer(name):
    stem = Path(name).stem
    m = re.search(r'(?i)\b(BE\s*\d+)\s*[-_ ]*U(?:nit)?\s*(\d+)', stem)
    lesson = re.search(r'(?i)(?:^|[^A-Z])L(?:esson)?\s*0*(\d+)', stem)
    return {'book': m.group(1).upper().replace(' ', '') if m else '', 'unit': int(m.group(2)) if m else None,
            'lessonNumber': int(lesson.group(1)) if lesson else None,
            'title': stem, 'lessonType': normalize_lesson_type(stem)}

def convert_docx(src: Path, dest: Path):
    script = '''on run argv
set inputName to item 1 of argv
set outputPath to item 2 of argv
set hfsOutput to (POSIX file outputPath) as text
tell application "Microsoft Word"
 repeat 60 times
  if exists document inputName then exit repeat
  delay 0.25
 end repeat
 if not (exists document inputName) then error "Word 未能打开临时文档"
 save as document inputName file name hfsOutput file format format PDF
 close document inputName saving no
end tell
end run'''
    # Launch Services grants Word access to the folder and its document.
    for path in (src.parent, src):
        subprocess.run(['open', '-a', 'Microsoft Word', str(path)], check=True,
                       capture_output=True, text=True, timeout=30)
    p = subprocess.run(['osascript', '-e', script, src.name, str(dest)],
                       capture_output=True, text=True, timeout=180)
    if p.returncode or not dest.exists(): raise RuntimeError('Word 转 PDF 失败：' + (p.stderr.strip() or p.stdout.strip()))

def word_workdir():
    # Word can read and write inside its own sandbox without requesting each file.
    path = Path.home() / 'Library/Containers/com.microsoft.Word/Data/Documents/BeaversTeachingResources'
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except PermissionError:
        # Some managed Macs deny other apps access to Word's container.
        fallback = ROOT / 'word-conversion-work'
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

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
        with tempfile.TemporaryDirectory(prefix='convert-', dir=word_workdir()) as td:
            src, out = Path(td)/f'source-{uuid.uuid4().hex}.docx', Path(td)/'output.pdf'
            src.write_bytes(raw); convert_docx(src, out); pdf = out.read_bytes()
    if pdf is not None and not pdf.startswith(b'%PDF-'): raise ValueError('PDF 文件无效')
    blob = f'{rid}-v{version}-view.bin' if pdf is not None else None
    if pdf is not None: (DATA/'blobs'/blob).write_bytes(seal(pdf, key))
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    item = {'id': rid, 'title': str(fields.get('title') or Path(original).stem).strip(),
            'book': str(fields.get('book') or '').strip(), 'unit': fields.get('unit'),
            'lessonNumber': fields.get('lessonNumber') or infer(original)['lessonNumber'],
            'lessonType': normalize_lesson_type(fields.get('lessonType')),  'category': str(fields.get('category') or '教案').strip(),
            'download': bool(fields.get('download', True)), 'filename': Path(original).stem + '.pdf',
            'originalFilename': original, 'originalBlob': original_blob, 'viewable': pdf is not None,
            'version': version, 'updatedAt': now, 'blob': blob, 'size': len(raw), 'status': 'published',
            'displayTitle': old.get('displayTitle', '') if old else '',
            'history': (old.get('history', []) + [{k: old.get(k) for k in ('version','blob','originalBlob','updatedAt','filename','originalFilename')}]) if old else []}
    if old: obj['resources'].remove(old)
    obj['resources'].append(item)
    note = str(fields.get('note') or ('资源已更新' if old else '新增资源')).strip()
    obj['updates'].insert(0, {'id': str(uuid.uuid4()), 'resourceId': rid, 'title': item['title'], 'text': note,
                              'kind': 'updated' if old else 'created', 'createdAt': now, 'visible': bool(fields.get('showUpdate', True))})
    return item

def rename_resource(obj, resource_id, display_title):
    title = str(display_title or '').strip()
    match = re.fullmatch(r'(?i)(BE\d+)\s*[-_ ]*U(\d+)\s*[-_ ]*(?:L(\d+))?\s*(一周一次|一周两次|每天上课|教材)', title)
    if not match: raise ValueError('请按 BE1U1L1一周两次 的格式输入名称')
    item = next((r for r in obj['resources'] if r['id'] == resource_id), None)
    if item is None: raise ValueError('找不到该资源')
    book, unit, lesson, course_type = match.groups()
    item.update({'book':book.upper(), 'unit':int(unit), 'lessonNumber':int(lesson) if lesson else None,
                 'lessonType':course_type, 'displayTitle':f'{book.upper()}U{int(unit)}' + (f'L{int(lesson)}' if lesson else '') + course_type})
    return item

def delete_resources(obj, resource_ids):
    ids = set(resource_ids)
    if not ids or len(ids) != len(resource_ids): raise ValueError('请选择有效的资源')
    removed = [r for r in obj['resources'] if r['id'] in ids]
    if len(removed) != len(ids): raise ValueError('部分资源不存在')
    blobs = set()
    for item in removed:
        for version in [item] + item.get('history', []):
            blobs.update(name for name in (version.get('blob'), version.get('originalBlob')) if name)
    obj['resources'] = [r for r in obj['resources'] if r['id'] not in ids]
    obj['updates'] = [u for u in obj['updates'] if u.get('resourceId') not in ids]
    return blobs

def _github_request(path, token, method='GET', payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request('https://api.github.com' + path, data=body, method=method,
        headers={'Accept':'application/vnd.github+json', 'Authorization':'Bearer ' + token,
                 'Content-Type':'application/json', 'User-Agent':'BeaversTeachingResources'})
    # The python.org macOS build may have no default CA bundle; keep TLS verification on.
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        mac_bundle = Path('/etc/ssl/cert.pem')
        context = ssl.create_default_context(cafile=str(mac_bundle) if mac_bundle.exists() else None)
    try:
        with urllib.request.urlopen(req, context=context, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try: detail = json.load(error).get('message', '')
        except Exception: detail = ''
        if error.code in (401, 403): raise ValueError('GitHub 发布令牌无效或缺少该仓库的 Contents 写入权限') from error
        if error.code == 409: raise ValueError('GitHub 仓库在发布期间发生变化，请重试') from error
        raise ValueError(f'GitHub 发布失败（{error.code}）：{detail}') from error

def publish(remote, token):
    if not re.fullmatch(r'https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?', remote): raise ValueError('仅接受 GitHub HTTPS 仓库地址')
    if not token: raise ValueError('请先填写 GitHub 发布令牌（仅授予此仓库 Contents 读写权限）')
    owner, repo = re.fullmatch(r'https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?', remote).groups()
    prefix = f'/repos/{owner}/{repo}'
    head = _github_request(prefix + '/git/ref/heads/main', token)['object']['sha']
    commit = _github_request(prefix + '/git/commits/' + head, token)
    tree_sha = commit['tree']['sha']
    existing = _github_request(prefix + '/git/trees/' + tree_sha + '?recursive=1', token)
    known = {item['path']:item['sha'] for item in existing['tree'] if item['type'] == 'blob'}
    changes = []
    public_files = [SITE / name for name in ('index.html', 'app.js', 'style.css')]
    encrypted_files = [SITE / 'data' / name for name in ('config.json', 'catalog.bin')]
    encrypted_files += sorted((SITE / 'data' / 'blobs').glob('*.bin'))
    for path in public_files + encrypted_files:
        if not path.is_file() or path.is_symlink(): continue
        name = 'docs/' + path.relative_to(SITE).as_posix()
        raw = path.read_bytes()
        digest = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if known.get(name) == digest: continue
        blob = _github_request(prefix + '/git/blobs', token, 'POST',
            {'content':b64(raw), 'encoding':'base64'})
        changes.append({'path':name, 'mode':'100644', 'type':'blob', 'sha':blob['sha']})
    local_blobs = {'docs/data/blobs/' + path.name for path in encrypted_files[2:]}
    for name in known:
        if name.startswith('docs/data/blobs/') and name.endswith('.bin') and name not in local_blobs:
            changes.append({'path':name, 'mode':'100644', 'type':'blob', 'sha':None})
    if not changes: return {'changed':0, 'sha':head}
    new_tree = _github_request(prefix + '/git/trees', token, 'POST',
        {'base_tree':tree_sha, 'tree':changes})
    new_commit = _github_request(prefix + '/git/commits', token, 'POST',
        {'message':'Publish teaching resources', 'tree':new_tree['sha'], 'parents':[head]})
    _github_request(prefix + '/git/refs/heads/main', token, 'PATCH',
        {'sha':new_commit['sha'], 'force':False})
    return {'changed':len(changes), 'sha':new_commit['sha']}


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
