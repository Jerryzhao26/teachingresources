import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from admin import core
class CoreTests(unittest.TestCase):
 def test_encrypt_and_version(self):
  with tempfile.TemporaryDirectory() as d:
   olddata=core.DATA; core.DATA=Path(d)
   try:
    obj,key=core.catalog('correct horse battery staple')
    key=core.save_catalog(obj,'correct horse battery staple')
    pdf=b'%PDF-1.4\n%%EOF'
    a=core.add_resource(obj,key,'BE2-U5-每周两次.pdf',pdf,{'book':'BE2','unit':5,'lessonType':'每周两次','download':False})
    self.assertEqual(core.open_sealed((core.DATA/'blobs'/a['blob']).read_bytes(),key),pdf)
    b=core.add_resource(obj,key,'new.pdf',pdf,{'id':a['id'],'download':True})
    self.assertEqual(b['version'],2);self.assertEqual(len(b['history']),1)
    core.save_catalog(obj,'correct horse battery staple',key)
    before=(core.DATA/'blobs'/a['blob']).read_bytes()
    core.rotate_password('correct horse battery staple','new password long enough')
    self.assertEqual(before,(core.DATA/'blobs'/a['blob']).read_bytes())
    with self.assertRaises(Exception):core.catalog('correct horse battery staple')
    core.secure_reset('new password long enough','third password long enough')
    self.assertNotEqual(before,(core.DATA/'blobs'/a['blob']).read_bytes())
    loaded,_=core.catalog('third password long enough')
    self.assertTrue(loaded['resources'][0]['download'])
    with self.assertRaises(Exception):core.catalog('wrong password here')
   finally:core.DATA=olddata
 def test_infer(self):
  self.assertEqual(core.infer('BE2-U5-每周两次.docx')['unit'],5)
  self.assertEqual(core.infer('BE2-U5-每周两次.docx')['lessonType'],'一周两次')
  self.assertEqual(core.normalize_lesson_type('每天上课'),'每天上课')
  self.assertEqual(core.normalize_lesson_type('教材'),'教材')
 def test_publish_only_changed_files(self):
  with tempfile.TemporaryDirectory() as d:
   site=Path(d); (site/'data').mkdir(); (site/'data/catalog.bin').write_bytes(b'encrypted')
   calls=[]
   def github(path, token, method='GET', payload=None):
    calls.append((path,method,payload))
    if path.endswith('/git/ref/heads/main'): return {'object':{'sha':'old'}}
    if path.endswith('/git/commits/old'): return {'tree':{'sha':'tree'}}
    if path.endswith('/git/trees/tree?recursive=1'): return {'tree':[]}
    if path.endswith('/git/blobs'): return {'sha':'blob'}
    if path.endswith('/git/trees'): return {'sha':'newtree'}
    if path.endswith('/git/commits'): return {'sha':'newcommit'}
    return {}
   with patch.object(core,'SITE',site), patch.object(core,'_github_request',side_effect=github):
    result=core.publish('https://github.com/Jerryzhao26/teachingresources.git','token')
   self.assertEqual(result,{'changed':1,'sha':'newcommit'})
   self.assertEqual(calls[-1][1:],('PATCH', {'sha':'newcommit','force':False}))
if __name__=='__main__':unittest.main()