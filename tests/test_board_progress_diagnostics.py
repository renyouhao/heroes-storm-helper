"""Task/CD diagnostics run through fake board workers and the real JSONL ZIP writer."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.loot_native import GameTarget
from hots_chat.talent_broadcast import CooldownHitSnapshot, HitSnapshot, QuestHitSnapshot, StatHitSnapshot
from tests import test_board_service as service_tests


class ProgressServiceDiagnosticsTests(unittest.TestCase):
    enable = service_tests.BoardServiceTests.enable
    click = service_tests.BoardServiceTests.click

    def setUp(self):
        service_tests.BoardServiceTests.setUp(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.recorder = DiagnosticRecorder(Path(self.tmp.name) / 'logs', enabled=True)
        self.addCleanup(self.recorder.close)
        self.selected_kind = 'quest'
        self.events = []
        outer = self
        class Delegate:
            def snapshot(self):
                return outer.recorder.snapshot()
            def record(self, event, **fields):
                result = outer.recorder.record(event, **fields)
                if event == 'helper.click':
                    outer.events.append(fields)
                return result
        self.helper.diagnostics = Delegate()
        def poll(pid, hwnd, now):
            self.reads.append('board-worker')
            common = dict(pid=pid, hwnd=hwnd, match_token='match', player_slot=2,
                team_relation='ally', rect=(100, 100, 150, 150), observed_at=now,
                valid_until=now+1, panel_visible=True, mapping_trusted=True,
                player_name='PRIVATE-NAME', hero_name='PRIVATE-HERO')
            selected = {
                'talent': HitSnapshot(**common, tier_level=13, talent_id='fixture-talent',
                    talent_name='任务天赋', effect_summary='已核实效果。', selected=True,
                    quest_state='known', quest_progress=0),
                'stat': StatHitSnapshot(**common, metric_id='hero_damage', value=12),
                'quest': QuestHitSnapshot(**common, quest_id='fixture-quest', quest_name='任务',
                    state='known', progress=42),
                'cooldown': CooldownHitSnapshot(**common, ability_id='fixture-heroic', ability_name='大招',
                    state='cooldown', remaining_seconds=8.5),
            }
            hits = tuple(replace(hit, rect=(100, 100, 150, 150) if kind == self.selected_kind
                                 else (300 + index*100, 100, 350 + index*100, 150))
                         for index, (kind, hit) in enumerate(selected.items()))
            return SimpleNamespace(pid=pid, hwnd=hwnd, match_token='match', status='available',
                                   hits=hits, chat_active=False, menu_active=False)
        self.reader.poll = poll

    def rows(self):
        path = self.recorder.export_report(Path(self.tmp.name) / 'report.zip')
        with zipfile.ZipFile(path) as archive:
            return [json.loads(line) for name in archive.namelist() if name.startswith('helper.log')
                    for line in archive.read(name).decode('utf8').splitlines()]

    def test_each_hit_kind_and_verified_value_reaches_real_jsonl(self):
        self.enable()
        for index, kind in enumerate(('talent', 'stat', 'quest', 'cooldown'), 1):
            self.selected_kind = kind
            self.click()
            self.assertTrue(service_tests.until(lambda: len(self.events) >= index))
        rows = self.rows()
        reads = [row for row in rows if row['event'] == 'helper.read' and 'hit_count' in row]
        self.assertTrue(reads)
        for row in reads:
            for kind in ('talent', 'stat', 'quest', 'cooldown'):
                self.assertEqual(row[kind + '_hit_count'], 1)
        clicks = [row for row in rows if row['event'] == 'helper.click']
        self.assertEqual([row['hit_kind'] for row in clicks], ['talent', 'stat', 'quest', 'cooldown'])
        self.assertEqual(clicks[0]['quest_state'], 'known')
        self.assertEqual(clicks[0]['quest_progress'], 0)
        self.assertNotIn('quest_progress', clicks[1])
        self.assertEqual(clicks[2]['quest_progress'], 42)
        self.assertEqual(clicks[3]['cooldown_state'], 'cooldown')
        self.assertEqual(clicks[3]['cooldown_seconds'], 8.5)
        summaries = json.dumps([row for row in rows if row['event'] != 'replay.frame'])
        self.assertNotIn('PRIVATE-NAME', summaries)
        self.assertNotIn('PRIVATE-HERO', summaries)

    def test_disabled_diagnostics_does_not_collect_per_kind_summaries(self):
        self.recorder.set_enabled(False)
        self.enable()
        self.click()
        self.assertTrue(service_tests.until(lambda: len(self.reads) >= 1))
        self.assertEqual(self.events, [])

    def test_failed_quests_keep_slot_and_item_attribution_in_real_zip(self):
        from hots_chat.acquisition.board_source import _BoardMemory, _quest_progress
        from tests.test_board_progress import ProgressMemory
        memory = ProgressMemory()
        first = memory.add_baseline(index=0, value=2, text='3')
        second = memory.add_baseline(index=1, value=5, text='5', visible=False)
        memory.set_talent_progress(1, 2, 'PRIVATE-QUEST-LABEL')
        wrapped = _BoardMemory(memory, diagnostics=self.recorder, diagnostic_source='broadcast')
        for record, slot, index in ((first, 3, 0), (second, 7, 1)):
            state, value, _ = _quest_progress(wrapped, memory.base, record['frame'], baseline=True,
                player_slot=slot, target_index=index, hero_id='HeroFixture',
                target_id='BaselineFixture' + str(index))
            self.assertEqual(state, 'unknown')
            self.assertIsNone(value)
        _quest_progress(wrapped, memory.base, memory.icon, player_slot=11, target_index=2,
            hero_id='HeroFixture', target_id='TalentFixture')
        rows = self.rows()
        failures = [row for row in rows if row['event'] == 'helper.read' and row.get('stage') == 'quest']
        attributed = {(row['player_slot'], row['hit_kind'], row.get('quest_index'), row.get('tier_id')):
                      row['progress_reason'] for row in failures}
        self.assertEqual(attributed, {(3, 'quest', 0, None): 'value_mismatch',
                                     (7, 'quest', 1, None): 'hidden_label',
                                     (11, 'talent', None, 2): 'non_numeric_label'})
        frames = [row['payload'] for row in rows if row['event'] == 'replay.frame'
                  and row.get('operation') == 'quest_progress']
        self.assertEqual(len(frames), 3)
        parser_keys = {'raw_state', 'raw_progress', 'label_count', 'label_visible', 'label_text', 'stable'}
        for payload in frames:
            self.assertEqual(set(payload['input']), parser_keys)
            self.assertEqual(set(payload['context']), {'player_slot', 'kind', 'index', 'hero_id', 'target_id'})
            self.assertTrue(payload['context']['hero_id'].isascii())
            self.assertTrue(payload['context']['target_id'].isascii())
        self.assertNotIn('PRIVATE-QUEST-LABEL', json.dumps(rows))

    def test_each_failed_board_getter_keeps_field_and_code_fingerprints_in_zip(self):
        from hots_chat.acquisition.board_source import BOARD_FIELD_GETTERS
        from tests.test_board_progress import BoardProgressTests
        fixture = BoardProgressTests()
        fixture.setUp()
        self.reader = fixture.reader
        self.target = GameTarget(77, 123)
        names = ('talent_quest_state', 'talent_quest_value', 'baseline_index',
                 'baseline_quest_state', 'baseline_quest_value', 'heroic_ability_link')
        expected_fingerprints = {}
        self.enable()
        try:
            for index, (field, (rva, original)) in enumerate(zip(names, BOARD_FIELD_GETTERS.items()), 1):
                changed = bytes([original[0] ^ 1]) + original[1:]
                fixture.memory.put(fixture.memory.base + rva, changed)
                self.click()
                self.assertTrue(service_tests.until(lambda: len(self.events) >= index))
                expected_fingerprints[field] = (hashlib.sha256(changed).hexdigest(),
                                                hashlib.sha256(original).hexdigest())
                fixture.memory.put(fixture.memory.base + rva, original)
        finally:
            self.helper.close()
        failures = [row for row in self.rows() if row['event'] == 'helper.read'
                    and row.get('stage') == 'board_profile']
        self.assertEqual(len(failures), 6)
        for row in failures:
            self.assertEqual(row['board_profile'], '98348-quest-cd-1')
            self.assertEqual(row['board_code_verified'], 0)
            self.assertEqual((row['board_code_fingerprint'], row['board_expected_fingerprint']),
                             expected_fingerprints[row['board_code_field']])

    def test_profile_diagnostics_reject_arbitrary_text_in_field_or_digest(self):
        self.recorder.record('helper.read', stage='board_profile',
            board_code_field='PRIVATE-FIELD', board_code_fingerprint='PRIVATE-ACTUAL',
            board_expected_fingerprint='PRIVATE-EXPECTED')
        self.recorder.record('helper.read', stage='board_profile',
            board_code_field='talent_quest_state', board_code_fingerprint='f' * 63,
            board_expected_fingerprint='g' * 64)
        rows = [row for row in self.rows() if row['event'] == 'helper.read']
        for row in rows:
            self.assertNotIn('board_code_fingerprint', row)
            self.assertNotIn('board_expected_fingerprint', row)
        self.assertNotIn('board_code_field', rows[0])
        self.assertEqual(rows[1]['board_code_field'], 'talent_quest_state')
        self.assertNotIn('PRIVATE-', json.dumps(rows))
