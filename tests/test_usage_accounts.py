from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from hots_chat.usage import BudgetError, BudgetLedger


NOW = datetime(2026, 10, 20, 12, tzinfo=timezone(timedelta(hours=8)))
ACCOUNT = '1968132000123456'
OTHER = '1968132000654321'


class AccountMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.db'
        self.ledger = BudgetLedger(self.path, limit=1000)

    def query(self, sql, args=()):
        with closing(sqlite3.connect(self.path)) as db, db:
            return db.execute(sql, args).fetchall()

    def test_first_binding_moves_all_months_and_carries_old_pending(self):
        september = NOW.replace(month=9)
        self.ledger.confirm_baseline('main', '2026-09', 10, september)
        self.ledger.reserve('main', 'pending-id', 'abc', september)
        self.ledger.confirm_baseline('main', '2026-10', 20, NOW)
        done = self.ledger.reserve('main', 'done-id', 'hello', NOW)
        self.ledger.settle(done, 'ok', NOW)
        self.assertTrue(self.ledger.bind_account(ACCOUNT, NOW))
        current = self.ledger.snapshot(ACCOUNT, NOW)
        self.assertEqual((current.local_reserved, current.external_used, current.remaining), (8, 20, 972))
        self.assertTrue(current.confirmed)
        self.assertEqual(self.query('SELECT external FROM months WHERE account=? AND month=?',
                                    (ACCOUNT, '2026-09')), [(10,)])
        for table in ('months', 'requests', 'carries'):
            self.assertEqual(self.query(f'SELECT COUNT(*) FROM {table} WHERE account=?', ('main',)), [(0,)])
        self.assertEqual(self.query('SELECT status FROM requests WHERE account=? AND id=?',
                                    (ACCOUNT, 'pending-id')), [('pending',)])
        november = NOW.replace(month=11, day=1)
        self.assertEqual(self.ledger.snapshot(ACCOUNT, november).local_reserved, 3)

    def test_rebinding_and_restart_never_reset_or_repeat_legacy_migration(self):
        self.ledger.confirm_baseline('main', '2026-10', 12, NOW)
        self.ledger.reserve('main', 'legacy', 'abc', NOW)
        self.assertTrue(self.ledger.bind_account(ACCOUNT, NOW))
        self.assertFalse(self.ledger.bind_account(ACCOUNT, NOW))
        self.assertFalse(BudgetLedger(self.path).bind_account(OTHER, NOW))
        self.assertEqual(self.ledger.snapshot(ACCOUNT, NOW).local_reserved, 3)
        self.assertEqual(self.ledger.snapshot(OTHER, NOW).local_reserved, 0)
        self.assertFalse(self.ledger.snapshot(OTHER, NOW).confirmed)
        # The persisted legacy alias cannot become a fresh budget after migration.
        self.ledger.reserve('main', 'legacy-after-migration', 'xy', NOW)
        self.assertEqual(self.ledger.snapshot(ACCOUNT, NOW).local_reserved, 5)

    def test_merge_preserves_both_baselines_requests_and_carries_on_id_collision(self):
        september = NOW.replace(month=9)
        for account, baseline, text in [('main', 10, 'abc'), (ACCOUNT, 20, 'hello')]:
            self.ledger.confirm_baseline(account, '2026-09', baseline, september)
            self.ledger.reserve(account, 'same-id', text, september)
        for account, baseline in [('main', 30), (ACCOUNT, 40)]:
            self.ledger.confirm_baseline(account, '2026-10', baseline, NOW)
        self.assertTrue(self.ledger.bind_account(ACCOUNT, NOW))
        snap = self.ledger.snapshot(ACCOUNT, NOW)
        self.assertEqual((snap.external_used, snap.local_reserved), (70, 8))
        self.assertTrue(snap.confirmed)
        self.assertEqual(self.query('SELECT COUNT(*) FROM requests WHERE account=?', (ACCOUNT,)), [(2,)])
        self.assertEqual(self.query('SELECT SUM(characters) FROM requests WHERE account=?', (ACCOUNT,)), [(8,)])
        self.assertEqual(self.query('SELECT external FROM months WHERE account=? AND month=?',
                                    (ACCOUNT, '2026-09')), [(30,)])

    def test_rollback_does_not_bind_or_move_any_data(self):
        self.ledger.confirm_baseline('main', '2026-10', 10, NOW)
        self.ledger.reserve('main', 'legacy', 'abc', NOW)
        with self.assertRaises(BudgetError):
            self.ledger.bind_account(ACCOUNT, NOW - timedelta(seconds=10))
        self.assertEqual(self.query('SELECT COUNT(*) FROM requests WHERE account=?', ('main',)), [(1,)])
        self.assertTrue(self.ledger.bind_account(OTHER, NOW))
        self.assertEqual(self.ledger.snapshot(OTHER, NOW).local_reserved, 3)

    def test_failed_sql_migration_rolls_back_metadata_and_all_rows(self):
        self.ledger.confirm_baseline('main', '2026-10', 10, NOW)
        self.ledger.reserve('main', 'legacy', 'abc', NOW)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TRIGGER fail_migration BEFORE INSERT ON requests WHEN NEW.account <> 'main' "
                       "BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END")
        with self.assertRaises(BudgetError):
            self.ledger.bind_account(ACCOUNT, NOW)
        self.assertEqual(self.query('SELECT COUNT(*) FROM requests WHERE account=?', ('main',)), [(1,)])
        self.assertEqual(self.query('SELECT external FROM months WHERE account=?', ('main',)), [(10,)])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP TRIGGER fail_migration')
        self.assertTrue(self.ledger.bind_account(OTHER, NOW))

    def test_empty_ledger_binding_is_recorded_before_any_future_account_switch(self):
        self.assertTrue(self.ledger.bind_account(ACCOUNT, NOW))
        self.assertFalse(self.ledger.bind_account(OTHER, NOW))
        self.assertFalse(self.ledger.snapshot(ACCOUNT, NOW).confirmed)

    def test_invalid_identity_cannot_claim_legacy_account(self):
        for account in ('main', '', ' 123', '0', '1' * 33, None, 123):
            with self.subTest(account=account):
                with self.assertRaises(BudgetError):
                    self.ledger.bind_account(account, NOW)
        self.assertTrue(self.ledger.bind_account(ACCOUNT, NOW))

    def test_simultaneous_first_bindings_assign_legacy_data_to_exactly_one_account(self):
        self.ledger.confirm_baseline('main', '2026-10', 10, NOW)
        self.ledger.reserve('main', 'legacy', 'abc', NOW)
        with ThreadPoolExecutor(max_workers=2) as pool:
            migrated = list(pool.map(lambda account: self.ledger.bind_account(account, NOW),
                                     [ACCOUNT, OTHER]))
        self.assertEqual(sorted(migrated), [False, True])
        snapshots = [self.ledger.snapshot(account, NOW) for account in (ACCOUNT, OTHER)]
        self.assertEqual(sorted(s.local_reserved for s in snapshots), [0, 3])
        self.assertEqual(sorted(s.external_used for s in snapshots), [0, 10])

    def test_invalid_legacy_or_target_counts_cannot_reduce_migrated_usage(self):
        self.ledger.confirm_baseline('main', '2026-10', 10, NOW)
        self.ledger.reserve('main', 'legacy', 'abc', NOW)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO months VALUES(?,?,?,1)', (ACCOUNT, '2026-10', -5))
        with self.assertRaises(BudgetError):
            self.ledger.bind_account(ACCOUNT, NOW)
        self.assertEqual(self.query('SELECT external FROM months WHERE account=?', ('main',)), [(10,)])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM months WHERE account=?', (ACCOUNT,))
            db.execute("UPDATE requests SET characters=-1 WHERE account='main'")
        with self.assertRaises(BudgetError):
            self.ledger.bind_account(OTHER, NOW)

    def test_baseline_integer_overflow_fails_closed_without_partial_migration(self):
        self.ledger.confirm_baseline('main', '2026-10', 2**63 - 1, NOW)
        self.ledger.confirm_baseline(ACCOUNT, '2026-10', 1, NOW)
        with self.assertRaises(BudgetError):
            self.ledger.bind_account(ACCOUNT, NOW)
        self.assertEqual(self.query('SELECT external FROM months WHERE account=?', ('main',)), [(2**63 - 1,)])


if __name__ == '__main__':
    unittest.main()
