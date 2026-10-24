"""解码检查保留实际观测值；不放开未知解码，也不额外读取游戏。"""
from pathlib import Path
import tempfile
import unittest

from hots_chat.acquisition.memory import GameProcess
from hots_chat.acquisition.talent_source import TalentReader
from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.replay import replay_archive
from tests.test_talent_source import Binding, TalentMemory


class ReaderReplayTests(unittest.TestCase):
    def make_reader(self, recorder=None, decoder=16):
        memory = TalentMemory()
        memory.pack(memory.base+0x3366118,'I',decoder)
        reader = TalentReader(binding_provider=lambda *_:Binding(),
            process_finder=lambda:[GameProcess(123,memory.path,98348)],
            game_factory=lambda _:memory,module_base_finder=lambda _:memory.base,
            creation_token_finder=lambda _:memory.creation,diagnostics=recorder)
        self.addCleanup(reader.close)
        return reader,memory

    def test_unsupported_decoder_records_actual_value_and_replays_rejection(self):
        tmp = tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        base=Path(tmp.name)
        recorder=DiagnosticRecorder(base/'logs',enabled=True);self.addCleanup(recorder.close)
        reader,_=self.make_reader(recorder)
        snapshot=reader.poll(123)
        self.assertEqual(snapshot.status,'unknown')
        self.assertEqual(reader.last_diagnostic['decoder_mode'],16)
        self.assertEqual(reader.last_diagnostic['supported_decoders'],','.join(map(str, range(16))))
        self.assertEqual(reader.last_diagnostic['api_active'],1)
        report=replay_archive(recorder.export_report(base/'recording.zip'))
        self.assertEqual(report['status'],'matched',report)
        self.assertEqual(report['details'][-1]['outcome'],'rejected')
        self.assertEqual(report['details'][-1]['reason'],'当前天赋解码方式尚未验证')

    def test_observation_does_not_add_memory_reads(self):
        class Recorder:
            def snapshot(self):return {'enabled':True}
            def record(self,*args,**kwargs):pass
        baseline,first=self.make_reader()
        observed,second=self.make_reader(Recorder())
        baseline.poll(123);observed.poll(123)
        self.assertEqual(first.calls,second.calls)

    def test_forged_expected_result_is_detected_by_actual_guard_logic(self):
        from hots_chat.reader_replay import replay_reader_frame
        payload=dict(schema=1,input=dict(api=1,talent=1,decoder=16),
            expected=dict(outcome='accepted',reason=''))
        report=replay_reader_frame('guards',payload)
        self.assertEqual(report['status'],'mismatch')

    def test_board_failure_keeps_same_decoder_observations_as_talents(self):
        from tests.test_board_source import BoardReaderTests
        fixture=BoardReaderTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        fixture.memory.pack(fixture.memory.base+0x3366118,'I',17)
        self.assertEqual(fixture.poll().status,'unknown')
        self.assertEqual(fixture.reader.last_diagnostic['decoder_mode'],17)
        self.assertEqual(fixture.reader.last_diagnostic['supported_decoders'],','.join(map(str, range(16))))
        self.assertEqual(fixture.reader.last_diagnostic['guard_stage'],'guards')

    def test_new_codecs_record_operands_and_replay_actual_decode(self):
        from tests.test_model_codecs import set_memory_codec
        import json
        import zipfile
        for decoder in range(16):
            with self.subTest(decoder=decoder):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                base = Path(tmp.name)
                recorder = DiagnosticRecorder(base/'logs', enabled=True)
                self.addCleanup(recorder.close)
                reader, memory = self.make_reader(recorder, decoder)
                set_memory_codec(memory, decoder)
                result = reader.poll(123)
                self.assertEqual(result.status, 'available', result.reason)
                archive = recorder.export_report(base/'recording.zip')
                report = replay_archive(archive)
                self.assertEqual(report['status'], 'matched', report)
                self.assertTrue(any(d['operation'] == 'decode_pointer' for d in report['details']))
                with zipfile.ZipFile(archive) as zipped:
                    rows = [json.loads(line) for line in zipped.read('helper.log').splitlines()]
                frames = [r for r in rows if r.get('operation') == 'decode_pointer']
                self.assertTrue(frames)
                self.assertEqual(len(frames[0]['payload']['input']['keys']), 2)
                self.assertEqual(frames[0]['payload']['expected']['pointer'], memory.model)

    def test_changed_codec_operands_keys_or_pointer_are_detected(self):
        from hots_chat.reader_replay import replay_reader_frame
        from hots_chat.acquisition.model_codecs import decode_pointer
        from copy import deepcopy
        keys = []

        def key(index):
            keys.append([index, 0])
            return 0

        pointer = decode_pointer(9, 0xdfffffff, 0, key)
        payload = dict(schema=1,
            input=dict(decoder=9, encoded_low=0xdfffffff, encoded_high=0, keys=keys),
            expected=dict(outcome='computed', pointer=pointer))
        self.assertEqual(replay_reader_frame('decode_pointer', payload)['status'], 'matched')
        for part in ('encoded_low', 'key_index', 'key_value', 'pointer'):
            altered = deepcopy(payload)
            if part == 'encoded_low':
                altered['input']['encoded_low'] ^= 1
            elif part == 'key_index':
                altered['input']['keys'][0][0] ^= 1
            elif part == 'key_value':
                altered['input']['keys'][0][1] ^= 1
            else:
                altered['expected']['pointer'] ^= 1
            self.assertEqual(replay_reader_frame('decode_pointer', altered)['status'],
                             'mismatch', part)

    def test_salt_is_required_recorded_and_checked_during_replay(self):
        from hots_chat.reader_replay import replay_reader_frame
        from hots_chat.acquisition.model_codecs import DECODER_SALT_POINTER_RVAS, decode_pointer
        from copy import deepcopy
        for decoder in DECODER_SALT_POINTER_RVAS:
            with self.subTest(decoder=decoder):
                keys = []

                def key(index):
                    keys.append([index, 0x9e3779b9])
                    return 0x9e3779b9

                pointer = decode_pointer(decoder, 0x12345678, 0xffffffff, key, salt=0x87654321)
                payload = dict(schema=1, input=dict(decoder=decoder, encoded_low=0x12345678,
                    encoded_high=0xffffffff, keys=keys, salt=0x87654321),
                    expected=dict(outcome='computed', pointer=pointer))
                self.assertEqual(replay_reader_frame('decode_pointer', payload)['status'], 'matched')
                missing = deepcopy(payload)
                del missing['input']['salt']
                self.assertEqual(replay_reader_frame('decode_pointer', missing)['status'], 'invalid')
                altered = deepcopy(payload)
                altered['input']['salt'] ^= 1
                self.assertEqual(replay_reader_frame('decode_pointer', altered)['status'], 'mismatch')
                altered['input']['salt'] = False
                self.assertEqual(replay_reader_frame('decode_pointer', altered)['status'], 'mismatch')
