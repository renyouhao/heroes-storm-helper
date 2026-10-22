"""只存请求元数据的保守账本；预留成功才允许网络调用。"""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from datetime import timedelta
import hashlib
import sqlite3
import uuid
from .clock import BEIJING, month_key
from .credentials import valid_account


class BudgetError(RuntimeError):
    pass


class BudgetExceeded(BudgetError):
    pass


@dataclass(frozen=True)
class Reservation:
    account: str
    request_id: str
    month: str
    characters: int


@dataclass(frozen=True)
class UsageSnapshot:
    month: str
    local_reserved: int
    external_used: int
    remaining: int
    confirmed: bool


def character_count(text):
    return max(len(text), len(text.encode('utf-16-le')) // 2)


class BudgetLedger:
    def __init__(self, path: Path, limit=900000):
        self.path, self.limit = Path(path), limit
        self.marker=self.path.with_suffix(self.path.suffix+'.initialized')
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            prior=self.marker.read_text(encoding='ascii').strip() if self.marker.exists() else None
            if prior is not None and (not self.path.exists() or self.path.stat().st_size==0):
                raise BudgetError('已初始化的用量账本丢失或被清空；请恢复原账本，云翻译已停止。')
            with self._db() as db:
                db.executescript('''
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS months(account TEXT, month TEXT, external INTEGER DEFAULT 0,
                  confirmed INTEGER DEFAULT 0, PRIMARY KEY(account,month));
                CREATE TABLE IF NOT EXISTS requests(account TEXT, id TEXT, month TEXT, characters INTEGER,
                  signature TEXT, status TEXT, PRIMARY KEY(account,id));
                CREATE TABLE IF NOT EXISTS carries(account TEXT, id TEXT, month TEXT, characters INTEGER,
                  PRIMARY KEY(account,id,month));
                ''')
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise BudgetError('用量账本校验失败，云翻译已停止。')
                identity=db.execute("SELECT value FROM meta WHERE key='ledger_id'").fetchone()
                if prior is not None and (not identity or identity[0]!=prior):
                    raise BudgetError('用量账本身份与初始化标记不符，云翻译已停止。')
                identity=identity[0] if identity else uuid.uuid4().hex
                db.execute("INSERT OR IGNORE INTO meta VALUES('ledger_id',?)",(identity,))
            if prior is None:self.marker.write_text(identity+'\n',encoding='ascii')
        except (OSError, UnicodeError, sqlite3.Error):
            raise BudgetError('用量账本无法安全读取，云翻译已停止。') from None

    @contextmanager
    def _db(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except sqlite3.Error:
            if db:
                db.rollback()
            raise BudgetError('用量账本读写失败，云翻译已停止。') from None
        except BaseException:
            if db:
                db.rollback()
            raise
        finally:
            if db:
                db.close()

    def _touch(self, db, now):
        month = month_key(now)
        stamp = now.timestamp()
        old = db.execute("SELECT value FROM meta WHERE key='last_time'").fetchone()
        if old and stamp < float(old[0]) - 2:
            raise BudgetError('检测到系统时间回退，请校准时间后重试。')
        db.execute("INSERT OR REPLACE INTO meta VALUES('last_time',?)", (str(max(stamp, float(old[0]) if old else stamp)),))
        # 未决请求可能已到达云端。跨月保守计入新账期，不退款。
        db.execute('''INSERT OR IGNORE INTO carries SELECT account,id,?,characters FROM requests
                      WHERE status='pending' AND month<>?''', (month, month))
        return month

    def _account(self, db, account):
        if account == 'main':
            bound = db.execute("SELECT value FROM meta WHERE key='legacy_main_account'").fetchone()
            if bound:
                if not valid_account(bound[0]):
                    raise BudgetError('历史账号绑定无效，云翻译已停止。')
                return bound[0]
        return account

    def bind_account(self, account, now):
        """Claim the pre-identity ledger once, while the controller has no in-flight work.

        Unknown overlap is charged conservatively: external baselines are added;
        colliding legacy request IDs are renamed together with all their carries.
        """
        if not valid_account(account):
            raise BudgetError('已验证主账号无效。')
        with self._db() as db:
            self._touch(db, now)
            bound = db.execute("SELECT value FROM meta WHERE key='legacy_main_account'").fetchone()
            if bound:
                if not valid_account(bound[0]):
                    raise BudgetError('历史账号绑定无效，云翻译已停止。')
                return False
            # Reject malformed legacy rows rather than allowing negative/NULL
            # values or an uncopyable ID to silently discard a past charge.
            for table, invalid in (
                ('months', "typeof(external)<>'integer' OR external<0 OR "
                           "typeof(confirmed)<>'integer' OR confirmed NOT IN (0,1)"),
                ('requests', "typeof(characters)<>'integer' OR characters<0 OR "
                             "typeof(id)<>'text' OR length(id)=0"),
                ('carries', "typeof(characters)<>'integer' OR characters<0 OR "
                            "typeof(id)<>'text' OR length(id)=0"),
            ):
                if db.execute(f'SELECT 1 FROM {table} WHERE account IN (?,?) AND ({invalid}) LIMIT 1',
                              ('main', account)).fetchone():
                    raise BudgetError('历史用量数据无效，无法安全迁移，云翻译已停止。')
            for month, external, confirmed in db.execute(
                    "SELECT month,external,confirmed FROM months WHERE account='main'").fetchall():
                existing = db.execute('SELECT external,confirmed FROM months WHERE account=? AND month=?',
                                      (account, month)).fetchone()
                if existing:
                    external += existing[0]
                    confirmed = bool(confirmed or existing[1])
                if external > 2**63 - 1:
                    raise BudgetError('历史用量合计超出安全范围，云翻译已停止。')
                db.execute('INSERT OR REPLACE INTO months VALUES(?,?,?,?)',
                           (account, month, external, int(bool(confirmed))))
            occupied = {row[0] for row in db.execute(
                'SELECT id FROM requests WHERE account=? UNION SELECT id FROM carries WHERE account=?',
                (account, account))}
            legacy_ids = [row[0] for row in db.execute(
                "SELECT id FROM requests WHERE account='main' UNION SELECT id FROM carries WHERE account='main'")]
            for request_id in legacy_ids:
                migrated_id = request_id
                while migrated_id in occupied:
                    migrated_id = 'legacy-main-' + uuid.uuid4().hex
                occupied.add(migrated_id)
                db.execute('''INSERT INTO requests SELECT ?,?,month,characters,signature,status
                              FROM requests WHERE account='main' AND id=?''',
                           (account, migrated_id, request_id))
                db.execute('''INSERT INTO carries SELECT ?,?,month,characters
                              FROM carries WHERE account='main' AND id=?''',
                           (account, migrated_id, request_id))
            for table in ('months', 'requests', 'carries'):
                db.execute(f"DELETE FROM {table} WHERE account='main'")
            db.execute("INSERT INTO meta VALUES('legacy_main_account',?)", (account,))
            return True

    def confirm_baseline(self, account, month, external_used, now):
        if not account.strip() or len(account) > 80 or type(external_used) is not int or external_used < 0:
            raise BudgetError('账号别名或基线无效。')
        with self._db() as db:
            account = self._account(db, account)
            if self._touch(db, now) != month:
                raise BudgetError('只能确认当前北京时间月份。')
            previous = db.execute('SELECT external FROM months WHERE account=? AND month=?', (account, month)).fetchone()
            if previous and external_used < previous[0]:
                raise BudgetError('同月其他用量基线不能调低。')
            db.execute('INSERT OR REPLACE INTO months VALUES(?,?,?,1)', (account, month, external_used))

    def _snapshot(self, db, account, month):
        row = db.execute('SELECT external,confirmed FROM months WHERE account=? AND month=?', (account, month)).fetchone()
        local = db.execute('SELECT COALESCE(SUM(characters),0) FROM requests WHERE account=? AND month=?', (account, month)).fetchone()[0]
        local += db.execute('SELECT COALESCE(SUM(characters),0) FROM carries WHERE account=? AND month=?', (account, month)).fetchone()[0]
        external, confirmed = row if row else (0, 0)
        return UsageSnapshot(month, local, external, max(0, self.limit - local - external), bool(confirmed))

    def snapshot(self, account, now):
        with self._db() as db:
            account = self._account(db, account)
            return self._snapshot(db, account, self._touch(db, now))

    def reserve(self, account, request_id, text, now):
        count = character_count(text)
        signature = hashlib.sha256(text.encode('utf-8')).hexdigest()
        with self._db() as db:
            account = self._account(db, account)
            month = self._touch(db, now)
            old = db.execute('SELECT month,characters,signature FROM requests WHERE account=? AND id=?', (account, request_id)).fetchone()
            if old:
                if old[2] != signature:
                    raise BudgetError('请求标识已被其他内容使用。')
                return Reservation(account, request_id, old[0], old[1])
            snap = self._snapshot(db, account, month)
            if not snap.confirmed:
                raise BudgetError('本月其他调用量尚未确认，当前只显示原文。')
            local = now.astimezone(BEIJING)
            if local.hour == 23 and local.minute == 59 and (local + timedelta(minutes=1)).month != local.month:
                raise BudgetError('月末最后一分钟暂停云请求。')
            if count > snap.remaining:
                raise BudgetExceeded('达到本机月度上限，已阻止云请求。')
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?)', (account, request_id, month, count, signature, 'pending'))
            return Reservation(account, request_id, month, count)

    def settle(self, reservation, status, finished_at):
        with self._db() as db:
            account = self._account(db, reservation.account)
            month = self._touch(db, finished_at)
            if month != reservation.month:
                db.execute('INSERT OR IGNORE INTO carries VALUES(?,?,?,?)', (account, reservation.request_id, month, reservation.characters))
            db.execute('UPDATE requests SET status=? WHERE account=? AND id=?',
                       (status if status in ('ok', 'error', 'timeout', 'cancelled') else 'error', account, reservation.request_id))
