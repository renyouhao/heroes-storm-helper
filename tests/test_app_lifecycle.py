from pathlib import Path
from contextlib import closing
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from hots_chat.app import PROJECT_ROOT,build_controller
from hots_chat.phrases import Phrase,PhraseBook
from hots_chat.paths import AppPaths

class AppTests(unittest.TestCase):
    def test_startup_no_cloud_and_all_workers_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);paths=AppPaths(p/'.env',p/'u.db',p/'l.db',p/'p.json')
            c=build_controller(paths,demo=True)
            try:
                self.assertTrue(c.idle)
                self.assertEqual(len(c.phrases.list()),12)
                with closing(sqlite3.connect(paths.language_file)) as db:
                    self.assertEqual(db.execute('SELECT kind FROM seeded').fetchall(),[('phrases',)])
            finally:c.close()
            self.assertFalse(c.thread.is_alive())

    def test_startup_needs_only_phrases_and_keeps_existing_language_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);paths=AppPaths(p/'.env',p/'u.db',p/'l.db',p/'p.json')
            resources=p/'resources';(resources/'data').mkdir(parents=True)
            shutil.copy2(PROJECT_ROOT/'data'/'phrases.json',resources/'data'/'phrases.json')
            book=PhraseBook(paths.language_file)
            book.save(Phrase('custom','团战','等我集合',{'en':'Wait for me.'},True,True,1))
            legacy=[('terms','old-term','invalid legacy term'),
                    ('corrections','old-correction','invalid legacy correction'),
                    ('templates','old-template','invalid legacy template')]
            with closing(sqlite3.connect(paths.language_file)) as db,db:
                db.executemany('INSERT INTO records VALUES(?,?,?)',legacy)
            with patch('hots_chat.app.PROJECT_ROOT',resources):
                try:c=build_controller(paths,demo=True)
                except FileNotFoundError as failure:self.fail(f'启动仍依赖已移除的资源：{failure.filename}')
            try:
                self.assertEqual(c.phrases.resolve('等我集合','en'),'Wait for me.')
                with closing(sqlite3.connect(paths.language_file)) as db:
                    self.assertEqual(db.execute("SELECT kind,id,payload FROM records WHERE kind!='phrases' ORDER BY kind").fetchall(),sorted(legacy))
                    self.assertEqual(db.execute('SELECT kind FROM seeded').fetchall(),[('phrases',)])
            finally:c.close()
