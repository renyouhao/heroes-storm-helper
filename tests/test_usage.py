from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from hots_chat.usage import BudgetLedger, BudgetError, BudgetExceeded

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone(timedelta(hours=8)))


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.db'
        self.ledger = BudgetLedger(self.path, limit=10)

    def confirm(self, external=0):
        self.ledger.confirm_baseline('main', '2026-10', external, NOW)

    def test_exact_limit_and_next_request_blocked(self):
        self.confirm(9)
        self.ledger.reserve('main', '1', '好', NOW)
        with self.assertRaises(BudgetExceeded):
            self.ledger.reserve('main', '2', '好', NOW)

    def test_restart_and_key_change_do_not_reset(self):
        self.confirm()
        r = self.ledger.reserve('main', '1', '😀', NOW)
        self.assertEqual(r.characters, 2)
        self.assertEqual(BudgetLedger(self.path, 10).snapshot('main', NOW).remaining, 8)
        self.ledger.reserve('main', '1', '😀', NOW)
        self.assertEqual(self.ledger.snapshot('main', NOW).local_reserved, 2)

    def test_unknown_baseline_and_corrupt_db_block(self):
        with self.assertRaises(BudgetError):
            self.ledger.reserve('main', '1', 'hi', NOW)
        bad = Path(self.tmp.name) / 'bad.db'
        bad.write_bytes(b'invalid sqlite')
        with self.assertRaises(BudgetError):
            BudgetLedger(bad)
    def test_missing_or_emptied_initialized_ledger_cannot_reset(self):
        self.confirm();self.ledger.reserve('main','first','hello',NOW)
        self.path.unlink()
        with self.assertRaises(BudgetError):BudgetLedger(self.path,10)
        self.path.write_bytes(b'')
        with self.assertRaises(BudgetError):BudgetLedger(self.path,10)

    def test_failed_and_timed_out_requests_stay_reserved(self):
        self.confirm()
        r = self.ledger.reserve('main', '1', 'hi', NOW)
        self.ledger.settle(r, 'timeout', NOW)
        self.assertEqual(self.ledger.snapshot('main', NOW).local_reserved, 2)

    def test_month_boundary_requires_confirmation(self):
        self.confirm()
        end = NOW.replace(day=31, hour=23, minute=59, second=10)
        with self.assertRaises(BudgetError):
            self.ledger.reserve('main', 'end', 'hi', end)

    def test_cross_month_and_unresolved_carry(self):
        self.confirm()
        r = self.ledger.reserve('main', '1', 'hi', NOW)
        next_month = NOW.replace(month=11, day=1)
        self.ledger.settle(r, 'ok', next_month)
        self.assertFalse(self.ledger.snapshot('main', next_month).confirmed)
        self.ledger.confirm_baseline('main', '2026-11', 0, next_month)
        self.assertEqual(self.ledger.snapshot('main', next_month).local_reserved, 2)

    def test_clock_rollback_blocks(self):
        self.confirm()
        with self.assertRaises(BudgetError):
            self.ledger.reserve('main', '1', 'hi', NOW - timedelta(seconds=10))

    def test_parallel_reservations_cannot_exceed_limit(self):
        self.confirm()
        def attempt(i):
            try:
                self.ledger.reserve('main', str(i), 'hi', NOW)
                return True
            except BudgetExceeded:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt, range(20))), 5)
