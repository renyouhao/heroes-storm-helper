"""显式保存的本机语言资料；不存聊天流。"""
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3


def text_field(value,limit=4000):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or '\x00' in value:
        raise ValueError('文字字段缺失、过长或含无效字符。')
    return value


def decode_import(data,kind,validate):
    if not isinstance(data,str) or len(data.encode('utf-8'))>1048576:raise ValueError('导入文件上限为 1 MiB。')
    try:
        def unique(pairs):
            d={}
            for k,v in pairs:
                if k in d:raise ValueError('JSON 字段重复。')
                d[k]=v
            return d
        value=json.loads(data,object_pairs_hook=unique)
        if not isinstance(value,dict) or set(value)!= {'version','kind','items'} or value['version']!=1 or value['kind']!=kind or not isinstance(value['items'],list) or len(value['items'])>1000:raise ValueError
        items=[validate(item) for item in value['items']]
        if len({item['id'] for item in items})!=len(items):raise ValueError
        return items
    except (ValueError,TypeError,KeyError):raise ValueError('语言资料格式无效；未导入任何条目。') from None


class LanguageStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id));
                               CREATE TABLE IF NOT EXISTS versions(kind TEXT PRIMARY KEY,revision INTEGER);
                               CREATE TABLE IF NOT EXISTS seeded(kind TEXT PRIMARY KEY);''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        try:
            with db:yield db
        finally:db.close()

    def list(self,kind):
        with self.connect() as db:return [json.loads(row[0]) for row in db.execute('SELECT payload FROM records WHERE kind=? ORDER BY id',(kind,))]

    def _save(self,db,kind,items):
        for item in items:
            old=db.execute('SELECT payload FROM records WHERE kind=? AND id=?',(kind,item['id'])).fetchone()
            item=dict(item);item['revision']=json.loads(old[0])['revision']+1 if old else 1
            db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',(kind,item['id'],json.dumps(item,ensure_ascii=False)))
        if db.execute('SELECT COUNT(*) FROM records WHERE kind=?',(kind,)).fetchone()[0]>1000:raise ValueError('每种语言资料最多 1000 条。')
        db.execute('INSERT INTO versions VALUES(?,1) ON CONFLICT(kind) DO UPDATE SET revision=revision+1',(kind,))

    def save_many(self,kind,items):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');self._save(db,kind,items)

    def delete(self,kind,item_id):
        with self.connect() as db:
            db.execute('DELETE FROM records WHERE kind=? AND id=?',(kind,item_id))
            db.execute('INSERT INTO versions VALUES(?,1) ON CONFLICT(kind) DO UPDATE SET revision=revision+1',(kind,))

    def seed_once(self,kind,items):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM seeded WHERE kind=?',(kind,)).fetchone():return
            if not db.execute('SELECT 1 FROM records WHERE kind=?',(kind,)).fetchone():self._save(db,kind,items)
            db.execute('INSERT INTO seeded VALUES(?)',(kind,))

    def revision(self,kind):
        with self.connect() as db:
            row=db.execute('SELECT revision FROM versions WHERE kind=?',(kind,)).fetchone()
            return row[0] if row else 0

    def export(self,kind,ids=None):
        items=[v for v in self.list(kind) if ids is None or v['id'] in ids]
        return json.dumps({'version':1,'kind':kind,'items':items},ensure_ascii=False,indent=2)
