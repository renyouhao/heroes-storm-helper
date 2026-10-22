"""有界异步诊断；业务线程不格式化日志或等待磁盘。"""
from datetime import datetime, timezone
import json
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
    'translation.request', 'translation.discarded', 'usage.reserve', 'usage.settle',
    'configuration.progress', 'ui.apply', 'hotkey.state', 'runtime.exception'))
_TEXT = frozenset(('stage', 'outcome', 'source', 'target', 'message_id', 'job_id',
    'request_ref', 'request_id', 'error_type', 'error_code', 'channel', 'key_id_last4',
    'provider_code', 'frames'))
_NUMBERS = frozenset(('generation', 'characters', 'elapsed_ms', 'queue_depth', 'count',
    'winerror', 'retry_count', 'build'))
_BODIES = frozenset(('original', 'translated'))
_LOG_NAMES = ('helper.log',) + tuple(f'helper.log.{i}' for i in range(1, 5))


class DiagnosticRecorder:
    def __init__(self, directory, *, monotonic=time.monotonic, queue_size=256,
                 max_bytes=2 * 1024 * 1024):
        self.directory = Path(directory)
        self.monotonic = monotonic
        self.queue = queue.Queue(maxsize=queue_size)
        self.session_id = uuid.uuid4().hex
        self._max_bytes = max_bytes
        self._secrets = ()
        self._detail_until = 0.0
        self._dropped = 0
        self._closed = self._write_error = False
        self._contains_body = False
        self._stop = threading.Event()
        self._file = None
        self._size = self._batch = 0
        self._repeated = None
        self.thread = threading.Thread(target=self._run, name='diagnostic-writer', daemon=True)
        self.thread.start()

    def register_secrets(self, *values):
        # 换Key后仍保留本进程旧值，避免已排队的详细正文泄露旧凭据。
        additions = tuple(v for v in values if isinstance(v, str) and v and v not in self._secrets)
        self._secrets += additions

    def enable_details(self, enabled):
        self._detail_until = self.monotonic() + 600 if enabled else 0.0

    def snapshot(self):
        return {'enabled': not self._closed and not self._write_error,
                'details_enabled': self.monotonic() < self._detail_until,
                'dropped': self._dropped, 'write_error': self._write_error,
                'version': __version__, 'contains_body': self._contains_body}

    def record(self, event, **fields):
        if self._closed or self._write_error or event not in EVENTS:
            return False
        data = {'event': event, 'time': time.time(), 'session_id': self.session_id}
        detailed = self.monotonic() < self._detail_until
        for key, value in fields.items():
            if key in _TEXT and isinstance(value, str):
                # 固定结构字段不允许路径/正文无限扩展；认证值后续仍统一脱敏。
                data[key] = self._redact(value)[:512 if key == 'frames' else 128]
            elif key in _NUMBERS and type(value) in (int, float):
                if value == value and abs(value) < 1e15:
                    data[key] = value
            elif detailed and key in _BODIES and isinstance(value, str):
                data[key] = self._redact(value)[:256]
                data[key + '_truncated'] = len(value) > 256
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
                path.unlink()
        path = self.directory / _LOG_NAMES[0]
        if path.is_symlink():
            raise OSError('Unsafe diagnostic path')
        self._file = path.open('ab')
        self._size = self._file.tell()

    def _write_line(self, line):
        data = (line + '\n').encode('utf-8')
        if len(data) > 4096:
            self._dropped += 1
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
                    source.replace(target)
            self._open()
        self._file.write(data)
        self._size += len(data)
        self._batch += 1
        if self._batch >= 32:
            self._flush()

    def _write(self, data):
        data = {key: self._redact(value) for key, value in data.items()}
        if any(key in data for key in _BODIES):
            self._contains_body = True
        self._write_line(json.dumps(data, ensure_ascii=False, separators=(',', ':')))

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
            if {k: v for k, v in old.items() if k != 'time'} == {k: v for k, v in row.items() if k != 'time'}:
                self._repeated = (old, count + 1, row['time'])
                return
        self._flush_repeated()
        self._write(row)
        if repeatable:
            self._repeated = (row, 1, row['time'])

    def _export(self, destination):
        if self._write_error:
            raise OSError('诊断日志不可写，请检查用户数据目录后重试。')
        self._flush_repeated()
        self._flush()
        files = [(name, self.directory / name) for name in _LOG_NAMES]
        files = [(name, path) for name, path in files if path.is_file() and not path.is_symlink()]
        # 旧运行留下的日志也可能有正文，摘要以实际导出材料为准。
        contents = [(name, path.read_bytes()) for name, path in files]
        contains_body = any(b'"original":' in data or b'"translated":' in data for _, data in contents)
        summary = dict(self.snapshot(), contains_body=contains_body, schema=1,
                       exported_at=datetime.now(timezone.utc).isoformat(), session_id=self.session_id)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.zip.tmp', delete=False) as stream:
                temporary = Path(stream.name)
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('summary.json', json.dumps(summary, ensure_ascii=False, indent=2))
                for name, data in contents:
                    archive.writestr(name, data)
            temporary.replace(destination)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def export_report(self, destination):
        if self._closed or self._write_error:
            raise OSError('诊断日志不可用，请重新启动助手后重试。')
        destination = Path(destination)
        if destination.suffix.lower() != '.zip':
            raise ValueError('请选择 ZIP 文件。')
        done, result = threading.Event(), []
        try:
            self.queue.put((destination, done, result), timeout=2)
        except queue.Full:
            raise OSError('日志繁忙，请稍后重试。') from None
        if not done.wait(10):
            raise OSError('日志导出超时，请稍后重试。')
        if result:
            raise OSError('日志导出失败，请检查保存位置后重试。')
        return destination

    def _run(self):
        flushed = time.monotonic()
        try:
            while not self._stop.is_set() or not self.queue.empty():
                try:
                    item = self.queue.get(timeout=.1)
                except queue.Empty:
                    item = None
                try:
                    if isinstance(item, tuple):
                        destination, done, result = item
                        try:
                            self._export(destination)
                        except (OSError, ValueError):
                            result.append(False)
                        finally:
                            done.set()
                    elif item is not None and not self._write_error:
                        self._consume(item)
                    if time.monotonic() - flushed >= 1:
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
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=max(0, timeout))
