from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from hots_chat.phrases import Phrase,PhraseBook

class PhraseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'language.db';self.book=PhraseBook(self.path)
        self.p=Phrase('1','机制','先撤退',{'ko':'후퇴해요','en':'Retreat'},False,False,1)
    def test_reviewed_only_and_favorite_persist(self):
        self.book.save(self.p)
        self.assertIsNone(self.book.resolve('先撤退','ko'))
        self.book.save(replace(self.p,reviewed=True,favorite=True))
        self.assertEqual(PhraseBook(self.path).resolve('先撤退','ko'),'후퇴해요')
        self.assertTrue(self.book.list()[0].favorite)
    def test_import_rejects_bad_schema_and_is_atomic(self):
        self.book.save(self.p);data=json.loads(self.book.export_json());data['items'].append({'bad':1})
        with self.assertRaises(ValueError):self.book.import_json(json.dumps(data))
        self.assertEqual(len(self.book.list()),1)
        with self.assertRaises(ValueError):self.book.import_json('x'*1048577)
    def test_markup_plain_text_and_delete(self):
        p=replace(self.p,source_text='<script>hi</script>')
        self.book.save(p);self.assertEqual(self.book.list(query='<script>')[0].source_text,p.source_text)
        self.book.delete(p.id);self.assertEqual(self.book.list(),[])
