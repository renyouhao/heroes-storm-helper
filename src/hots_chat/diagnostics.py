"""有界异步诊断；业务线程不格式化日志或等待磁盘。"""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import queue
import tempfile
import threading
import time
import uuid
import zipfile

from . import __version__


EVENTS = frozenset(('app.start', 'app.stop', 'capture.state', 'message.received',
    'translation.route', 'translation.submitted', 'translation.result', 'translation.failure',
    'translation.request', 'translation.discarded',
    'configuration.progress', 'ui.apply', 'hotkey.state', 'runtime.exception',
    'helper.read', 'helper.click', 'diagnostic.start', 'replay.frame'))
_TEXT = frozenset(('stage', 'outcome', 'source', 'target', 'message_id', 'job_id',
    'request_ref', 'request_id', 'error_type', 'error_code', 'channel', 'key_id_last4',
    'provider_code', 'frames', 'reason', 'reader_status', 'coordinate_reason',
    'chat_state', 'menu_state', 'foreground_state', 'hit_kind', 'identity_reason', 'identity_error_type',
    'component', 'operation', 'version', 'guard_stage', 'supported_decoders',
    'decoder_code_fingerprint', 'decoder_expected_fingerprint',
    'quest_state', 'cooldown_state', 'progress_reason', 'cooldown_reason',
    'quest_read_stage', 'cooldown_read_stage', 'board_profile', 'board_code_field',
    'board_code_fingerprint', 'board_expected_fingerprint'))
_TEXT = _TEXT | frozenset(('last_rejection_reason', 'source_message_id', 'target_language'))
_NUMBERS = frozenset(('generation', 'characters', 'elapsed_ms', 'queue_depth', 'count',
    'winerror', 'retry_count', 'build', 'expected_build', 'read_calls', 'read_bytes',
    'client_width', 'client_height', 'origin_x', 'origin_y', 'render_width', 'render_height',
    'raw_talent_count', 'raw_stat_count', 'coordinate_rejected', 'metadata_rejected',
    'raw_quest_count', 'raw_cooldown_count', 'progress_rejected', 'cooldown_rejected', 'cooldown_alias_count',
    'talent_hit_count', 'stat_hit_count', 'quest_hit_count', 'cooldown_hit_count',
    'quest_progress', 'quest_raw_state', 'quest_raw_progress', 'cooldown_seconds', 'player_slot',
    'quest_node_checks', 'cooldown_node_checks', 'label_visible', 'label_numeric', 'label_matches',
    'board_code_verified', 'tier_id', 'quest_index',
    'hit_count', 'candidate_count', 'click_x', 'click_y', 'event_age_ms', 'attempt_id',
    'valid_snapshot_count', 'invalid_candidate_count', 'empty_snapshot_count',
    'retry_snapshot_count', 'ignored_snapshot_count',
    'schema', 'replay_schema', 'max_total_bytes', 'max_event_bytes',
    'decoder_mode', 'expected_decoder', 'api_active', 'talent_active', 'decoder_code_verified'))
_NUMBERS = _NUMBERS | frozenset(('capture_schema', 'scan_id', 'reader_id', 'scan_region_count',
    'requested_bytes', 'short_read_count', 'process_count', 'message_count', 'gap_count',
    'published_count', 'child_generation', 'nodes_checked_count', 'prefix_rejected_count',
    'timestamp_format_rejected_count', 'channel_unsupported_count', 'channel_filtered_count',
    'message_format_rejected_count', 'parsed_message_count', 'candidate_read_failure_count',
    'candidate_layout_rejected_count', 'candidate_link_rejected_count', 'candidate_budget_rejected_count',
    'timestamped_message_count', 'untimestamped_message_count',
    'native_lobby_schema', 'native_node_checks', 'native_message_count', 'native_system_count',
    'native_party_count', 'native_whisper_count', 'native_unsupported_count', 'native_unreadable_count',
    'native_invalid_count', 'native_before_start_count', 'native_dedup_count', 'native_parsed_count',
    'native_draft_count', 'native_draft_rejected_count', 'native_draft_context_checks',
    'native_draft_context_changes', 'native_draft_context_rejected'))
_ENUM_TEXT = {
    'target_language': frozenset(('zh', 'en')),
    'quest_state': frozenset(('', 'known', 'complete', 'unknown', 'untracked')),
    'cooldown_state': frozenset(('', 'cooldown', 'ready', 'unknown', 'untracked')),
    'progress_reason': frozenset(('invalid_observations', 'changed_during_read', 'no_quest',
        'untracked', 'completed', 'unknown_state', 'missing_or_ambiguous_label', 'hidden_label',
        'non_numeric_label', 'value_mismatch', 'verified')),
    'cooldown_reason': frozenset(('cooldown_label_positive_seconds', 'cooldown_label_hidden',
        'cooldown_label_not_positive_seconds', 'charge_ability_unsupported', 'ability_reference_unresolved',
        'heroic_ability_unselected', 'cooldown_binding_untrusted', 'cooldown_display_unobserved',
        'heroic_ability_unresolved')),
    'quest_read_stage': frozenset(('value',)),
    'cooldown_read_stage': frozenset(('cooldown_display',)),
    'board_profile': frozenset(('98348-quest-cd-1',)),
    'board_code_field': frozenset(('talent_quest_state', 'talent_quest_value', 'baseline_index',
        'baseline_quest_state', 'baseline_quest_value', 'heroic_ability_link')),
}
_SHA256_TEXT = frozenset(('board_code_fingerprint', 'board_expected_fingerprint'))
_BODIES = frozenset(('original', 'translated'))
_LOG_NAMES = ('helper.log',) + tuple(f'helper.log.{i}' for i in range(1, 5))
MAX_EVENT_BYTES = 1024 * 1024
MAX_STRING_CHARS = 16384
_FLAGS = frozenset(('original_truncated', 'translated_truncated', 'payload_truncated',
                    'payload_redacted', 'payload_invalid', 'summary'))
_META = frozenset(('event', 'time', 'session_id', 'sequence', 'monotonic', 'thread_id',
                   'recording_epoch', 'last_time'))
_CREDENTIAL_KEYS = frozenset(('apikey', 'token', 'accesstoken', 'refreshtoken',
    'authorization', 'password', 'secret', 'credentials', 'credential', 'privatekey',
    'clientsecret', 'proxycredentials', 'proxypassword'))


class _PayloadFilter:
    """只复制有界 JSON 值；不调用外部对象的序列化/字符串转换方法。"""
    def __init__(self, redact):
        self.redact = redact
        self.nodes = 12000
        self.characters = MAX_EVENT_BYTES
        self.truncated = self.redacted = self.invalid = False
        self.ancestors = set()

    def text(self, value):
        clean = self.redact(value)
        self.redacted |= clean != value
        limit = min(MAX_STRING_CHARS, self.characters)
        self.truncated |= len(clean) > limit
        clean = clean[:limit]
        self.characters -= len(clean)
        return clean

    def copy(self, value, depth=0):
        self.nodes -= 1
        if self.nodes < 0 or depth > 16:
            self.truncated = True
            return None
        if value is None or type(value) is bool:
            return value
        if type(value) in (int, float):
            if abs(value) < 1e100 and (type(value) is int or math.isfinite(value)):
                return value
            self.invalid = True
            return None
        if type(value) is str:
            return self.text(value)
        if type(value) not in (dict, list) or id(value) in self.ancestors:
            self.invalid = True
            return None
        self.ancestors.add(id(value))
        try:
            result = {} if type(value) is dict else []
            iterator = value.items() if type(value) is dict else enumerate(value)
            for key, child in iterator:
                if self.nodes <= 0 or self.characters <= 0:
                    self.truncated = True
                    break
                if type(value) is dict:
                    self.nodes -= 1
                    if type(key) is not str:
                        self.invalid = True
                        continue
                    name = self.text(key)
                    credential = ''.join(c for c in key.lower() if c.isalnum()) in _CREDENTIAL_KEYS
                    if credential:
                        result[name] = '[redacted]'
                        self.redacted = True
                    else:
                        result[name] = self.copy(child, depth + 1)
                else:
                    result.append(self.copy(child, depth + 1))
            return result
        finally:
            self.ancestors.remove(id(value))


class _ExportRequest:
    def __init__(self,destination):
        self.destination=destination
        self.done=threading.Event()
        self.lock=threading.Lock()
        self.cancelled=self.published=self.failed=False


class DiagnosticRecorder:
    def __init__(self, directory, *, monotonic=time.monotonic, queue_size=256,
                 max_bytes=None, max_total_bytes=100 * 1024 * 1024,
                 enabled=False, export_timeout=120.0):
        self.directory = Path(directory)
        self.monotonic = monotonic
        self.queue = queue.Queue(maxsize=queue_size)
        self.session_id = uuid.uuid4().hex
        if type(max_total_bytes) is not int or max_total_bytes <= 0:
            raise ValueError('max_total_bytes must be a positive integer')
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes <= 0):
            raise ValueError('max_bytes must be a positive integer')
        self._max_total_bytes = max_total_bytes
        # max_bytes 仍表示单段上限；总上限独立约束所有五个日志。
        self._max_bytes = min(max_bytes or max(1, max_total_bytes // 5), max_total_bytes)
        self._secrets = ()
        self._dropped = 0
        self._overwritten = self._overwritten_bytes = self._truncated = self._oversized = 0
        self._payload_redacted = self._payload_invalid = False
        self._incomplete_frames = 0
        self._record_lock = threading.RLock()
        self._sequence = 0
        self.recording_epoch = uuid.uuid4().hex
        self._closed = self._write_error = False
        self._enabled=bool(enabled)
        self._export_timeout=export_timeout
        self._worker_lock=threading.Lock()
        self._contains_body = False
        self._stop = threading.Event()
        self._file = None
        self._size = self._batch = 0
        self._repeated = None
        self.thread=None
        if self._enabled:
            self._ensure_worker()
            self._record_start()

    def _record_start(self):
        self.record('diagnostic.start', version=__version__, schema=2, replay_schema=1,
                    max_total_bytes=self._max_total_bytes, max_event_bytes=MAX_EVENT_BYTES)

    def _ensure_worker(self):
        with self._worker_lock:
            if self.thread is None and not self._closed:
                self.thread=threading.Thread(target=self._run,name='diagnostic-writer',daemon=True)
                self.thread.start()

    def set_enabled(self,enabled):
        with self._record_lock:
            if self._closed or self._enabled == bool(enabled):return
            self._enabled=bool(enabled)
            if self._enabled:
                self.recording_epoch = uuid.uuid4().hex
                self._ensure_worker()
                self._record_start()
            else:
                try:self.queue.put_nowait(None)
                except queue.Full:pass

    def register_secrets(self, *values):
        # 换Key后仍保留本进程旧值，避免已排队的详细正文泄露旧凭据。
        additions = tuple(v for v in values if isinstance(v, str) and v and v not in self._secrets)
        self._secrets += additions

    def enable_details(self, enabled):
        """兼容旧调用；诊断正文现在与总开关一起启停。"""

    def snapshot(self):
        return {'enabled': self._enabled and not self._closed and not self._write_error,
                'dropped': self._dropped, 'write_error': self._write_error,
                'version': __version__, 'contains_body': self._contains_body,
                'schema': 2, 'replay_schema': 1, 'recording_epoch': self.recording_epoch,
                'max_total_bytes': self._max_total_bytes, 'max_event_bytes': MAX_EVENT_BYTES,
                'overwritten': self._overwritten, 'overwritten_bytes': self._overwritten_bytes,
                'truncated': self._truncated, 'oversized': self._oversized,
                'payload_redacted': self._payload_redacted, 'payload_invalid': self._payload_invalid,
                'incomplete_frames': self._incomplete_frames,
                'incomplete': bool(self._dropped or self._write_error or self._overwritten
                                   or self._truncated or self._oversized
                                   or self._payload_redacted or self._payload_invalid or self._incomplete_frames)}

    def record(self, event, **fields):
        if not self._enabled or self._closed or self._write_error or event not in EVENTS:
            return False
        data = {'event': event}
        for key, value in fields.items():
            if key in _ENUM_TEXT and (type(value) is not str or value not in _ENUM_TEXT[key]):
                continue
            if key in _SHA256_TEXT and (type(value) is not str or len(value) != 64
                    or any(character not in '0123456789abcdefABCDEF' for character in value)):
                continue
            if key in _TEXT and isinstance(value, str):
                # 固定结构字段不允许路径/正文无限扩展；认证值后续仍统一脱敏。
                data[key] = self._redact(value)[:512 if key == 'frames' else 128]
            elif key in _NUMBERS and type(value) in (int, float):
                if value == value and abs(value) < 1e15:
                    data[key] = value
            elif key in _BODIES and type(value) is str:
                clean = self._redact(value)
                data[key] = clean[:MAX_STRING_CHARS]
                data[key + '_truncated'] = len(clean) > MAX_STRING_CHARS
            elif event == 'replay.frame' and key == 'payload':
                filtered = _PayloadFilter(self._redact)
                data['payload'] = filtered.copy(value)
                data['payload_truncated'] = filtered.truncated
                data['payload_redacted'] = filtered.redacted
                data['payload_invalid'] = filtered.invalid or type(value) not in (dict, list)
        if event == 'replay.frame' and 'payload' not in data:
            data['payload_invalid'] = True
        with self._record_lock:
            if not self._enabled or self._closed or self._write_error:
                return False
            self._sequence += 1
            data.update(time=time.time(), session_id=self.session_id, sequence=self._sequence,
                        monotonic=self.monotonic(), thread_id=threading.get_ident(),
                        recording_epoch=self.recording_epoch)
            try:
                self.queue.put_nowait(data)
                return True
            except queue.Full:
                self._dropped += 1
                return False

    def _redact(self, value):
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, '[redacted]')
        return value

    def _open(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 7 * 24 * 3600
        for name in _LOG_NAMES:
            path = self.directory / name
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                self._remove_log(path)
        path = self.directory / _LOG_NAMES[0]
        if path.is_symlink():
            raise OSError('Unsafe diagnostic path')
        self._file = path.open('ab')
        self._size = self._file.tell()
        self._enforce_budget()

    def _remove_log(self, path):
        if path.is_symlink():
            raise OSError('Unsafe diagnostic path')
        if path.exists():
            size = path.stat().st_size
            path.unlink()
            if size:
                self._overwritten += 1
                self._overwritten_bytes += size

    def _enforce_budget(self, additional=0):
        """淘汰最旧段，升级留下的大文件也计入同一个总预算。"""
        older = []
        for name in reversed(_LOG_NAMES[1:]):
            path = self.directory / name
            if path.is_symlink():
                raise OSError('Unsafe diagnostic path')
            if path.is_file():
                older.append((path, path.stat().st_size))
        total = self._size + sum(size for _, size in older)
        for path, size in older:
            if total + additional <= self._max_total_bytes:
                break
            self._remove_log(path)
            total -= size
        if total + additional > self._max_total_bytes:
            if self._file is not None:
                self._file.close()
            path = self.directory / _LOG_NAMES[0]
            self._remove_log(path)
            self._file = path.open('ab')
            self._size = 0

    def _write_line(self, line):
        data = (line + '\n').encode('utf-8')
        if len(data) > min(MAX_EVENT_BYTES, self._max_total_bytes):
            self._oversized += 1
            return
        if self._file is None:
            self._open()
        if self._size and self._size + len(data) > self._max_bytes:
            self._file.close()
            self._file = None
            for index in range(4, 0, -1):
                target = self.directory / _LOG_NAMES[index]
                source = self.directory / _LOG_NAMES[index - 1]
                if target.is_symlink() or source.is_symlink():
                    raise OSError('Unsafe diagnostic path')
                if source.exists():
                    if index == 4:
                        self._remove_log(target)
                    source.replace(target)
            self._open()
        self._enforce_budget(len(data))
        self._file.write(data)
        self._size += len(data)
        self._batch += 1
        if self._batch >= 32:
            self._flush()

    def _write(self, data):
        data = self._safe_row(data)
        if any(key in data for key in _BODIES) or self._payload_has_body(data):
            self._contains_body = True
        if any(data.get(key) for key in ('payload_truncated', 'original_truncated', 'translated_truncated')):
            self._truncated += 1
        self._payload_redacted |= data.get('payload_redacted', False)
        self._payload_invalid |= data.get('payload_invalid', False)
        if self._partial_frame(data):self._incomplete_frames += 1
        self._write_line(json.dumps(data, ensure_ascii=False, separators=(',', ':')))

    def _safe_row(self, data):
        # 同时用于新写入与旧文件导出；导出前重新按最新密钥递归脱敏。
        allowed = _TEXT | _NUMBERS | _BODIES | _FLAGS | _META
        row = {}
        for key, value in data.items():
            if key in allowed and type(value) in (str, bool, int, float, type(None)):
                if type(value) is float and not math.isfinite(value):
                    continue
                row[key] = self._redact(value)
        if data.get('event') == 'replay.frame' and 'payload' in data:
            filtered = _PayloadFilter(self._redact)
            row['payload'] = filtered.copy(data['payload'])
            row['payload_redacted'] = bool(row.get('payload_redacted') or filtered.redacted)
            row['payload_truncated'] = bool(row.get('payload_truncated') or filtered.truncated)
            row['payload_invalid'] = bool(row.get('payload_invalid') or filtered.invalid
                                          or type(data['payload']) not in (dict, list))
        elif data.get('event') == 'replay.frame':
            row['payload_invalid'] = True
        return row

    @staticmethod
    def _partial_frame(row):
        payload=row.get('payload')
        return row.get('event')=='replay.frame' and isinstance(payload,dict) and (
            payload.get('complete') is False or bool(payload.get('incomplete')))

    @staticmethod
    def _payload_has_body(row):
        def contains(value):
            if type(value) is dict:
                return bool(_BODIES.intersection(value)) or any(contains(v) for v in value.values())
            return type(value) is list and any(contains(v) for v in value)
        return contains(row.get('payload'))

    def _flush(self):
        if self._file is not None:
            self._file.flush()
        self._batch = 0

    def _flush_repeated(self):
        if self._repeated is not None:
            row, count, last = self._repeated
            if count > 1:
                self._write(dict(row, count=count - 1, last_time=last, summary=True))
            self._repeated = None

    def _consume(self, row):
        repeatable = row['event'] in ('capture.state', 'runtime.exception')
        if repeatable and self._repeated is not None:
            old, count, _ = self._repeated
            ignored = {'time', 'sequence', 'monotonic', 'thread_id'}
            if {k: v for k, v in old.items() if k not in ignored} == {k: v for k, v in row.items() if k not in ignored}:
                self._repeated = (old, count + 1, row['time'])
                return
        self._flush_repeated()
        self._write(row)
        if repeatable:
            self._repeated = (row, 1, row['time'])

    def _export(self, request):
        if request.cancelled:return
        destination=request.destination
        if self._write_error:
            raise OSError('诊断日志不可写，请检查用户数据目录后重试。')
        self._flush_repeated()
        self._flush()
        if self._file is not None:
            self._file.close()
            self._file = None
        # 导出前也执行保留期清理，长期空闲的旧日志不随包带出。
        self._open()
        files = [(name, self.directory / name) for name in _LOG_NAMES]
        files = [(name, path) for name, path in files if path.is_file() and not path.is_symlink()]
        # 摘要依据实际导出数据生成，兼容旧格式并反映导出时的密钥脱敏。
        contains_body = False
        payload_redacted = payload_invalid = False
        retained_truncated = export_rejected = 0
        retained_incomplete = 0
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.zip.tmp', delete=False) as stream:
                temporary = Path(stream.name)
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name, path in files:
                    with path.open('rb') as source,archive.open(name,'w') as output:
                        while True:
                            if request.cancelled:return
                            line = source.readline(MAX_EVENT_BYTES + 1)
                            if not line:break
                            if len(line)>MAX_EVENT_BYTES:
                                while line and not line.endswith(b'\n'):
                                    line = source.readline(MAX_EVENT_BYTES + 1)
                                export_rejected += 1
                                continue
                            try:row=json.loads(line)
                            except (ValueError,TypeError,UnicodeError,RecursionError):
                                export_rejected += 1
                                continue
                            if not isinstance(row,dict) or row.get('event') not in EVENTS:
                                export_rejected += 1
                                continue
                            row = self._safe_row(row)
                            contains_body |= any(k in row for k in _BODIES) or self._payload_has_body(row)
                            payload_redacted |= row.get('payload_redacted', False)
                            payload_invalid |= row.get('payload_invalid', False)
                            retained_truncated += any(row.get(key) for key in ('payload_truncated',
                                                      'original_truncated', 'translated_truncated'))
                            retained_incomplete += self._partial_frame(row)
                            output.write((json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8'))
                summary = dict(self.snapshot(), contains_body=contains_body,
                    exported_at=datetime.now(timezone.utc).isoformat(), session_id=self.session_id,
                    payload_redacted=bool(self._payload_redacted or payload_redacted),
                    payload_invalid=bool(self._payload_invalid or payload_invalid),
                    truncated=max(self._truncated, retained_truncated), export_rejected=export_rejected,
                    incomplete_frames=max(self._incomplete_frames,retained_incomplete))
                summary['incomplete'] |= bool(payload_redacted or payload_invalid
                                              or retained_truncated or export_rejected or retained_incomplete)
                archive.writestr('summary.json', json.dumps(summary, ensure_ascii=False, indent=2))
            with request.lock:
                if request.cancelled:return
                temporary.replace(destination)
                request.published=True
                request.done.set()
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def export_report(self, destination):
        if self._closed or self._write_error:
            raise OSError('诊断日志不可用，请重新启动助手后重试。')
        destination = Path(destination)
        if destination.suffix.lower() != '.zip':
            raise ValueError('请选择 ZIP 文件。')
        request=_ExportRequest(destination)
        self._ensure_worker()
        try:
            self.queue.put(request, timeout=2)
        except queue.Full:
            raise OSError('日志繁忙，请稍后重试。') from None
        if not request.done.wait(self._export_timeout):
            with request.lock:
                if not request.published:
                    request.cancelled=True
                    raise OSError('日志导出超时，请稍后重试。')
        if request.failed:
            raise OSError('日志导出失败，请检查保存位置后重试。')
        return destination

    def _run(self):
        flushed = time.monotonic()
        try:
            while not self._stop.is_set() or not self.queue.empty():
                try:
                    item = self.queue.get(timeout=.1 if self._enabled else None)
                except queue.Empty:
                    item = None
                try:
                    if isinstance(item, _ExportRequest):
                        try:
                            self._export(item)
                        except (OSError, ValueError):
                            item.failed=True
                        finally:
                            item.done.set()
                    elif item is not None and not self._write_error:
                        self._consume(item)
                    if not self._enabled or time.monotonic() - flushed >= 1:
                        self._flush_repeated()
                        self._flush()
                        flushed = time.monotonic()
                except (OSError, ValueError, TypeError, OverflowError):
                    self._write_error = True
                    self._repeated = None
            if not self._write_error:
                self._flush_repeated()
                self._flush()
        finally:
            if self._file is not None:
                try:
                    self._file.close()
                except OSError:
                    self._write_error = True

    def close(self, timeout=1.0):
        self._closed = True
        self._stop.set()
        try:self.queue.put_nowait(None)
        except queue.Full:pass
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=max(0, timeout))
