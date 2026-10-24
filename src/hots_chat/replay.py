"""流式读取诊断ZIP，重放已录制的业务边界；不加载包内代码或调用外部服务。"""
import argparse
import json
from pathlib import Path
import zipfile

from . import __version__


_LOGS = tuple(f'helper.log.{i}' for i in range(4, 0, -1)) + ('helper.log',)
_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
_MAX_LINE_BYTES = 1024 * 1024
_MAX_FRAMES = 200_000
_MAX_DETAILS = 500
_COMPONENTS = frozenset(('translation', 'talent', 'board', 'reader'))


def _json(value):
    def invalid_constant(_):raise ValueError('non-finite JSON value')
    return json.loads(value, parse_constant=invalid_constant)


def _replay_frame(row):
    if ('operation' in row['payload'] and row['payload']['operation'] != row['operation']):
        return dict(status='invalid', reason='operation_mismatch')
    if row['component'] == 'translation':
        from .translation_replay import replay_translation_frame
        return replay_translation_frame(row['payload'])
    if row['component'] == 'reader':
        from .reader_replay import replay_reader_frame
        return replay_reader_frame(row['operation'],row['payload'])
    from .helper_replay import replay_helper_frame
    return replay_helper_frame(row['component'], row['operation'], row['payload'])


def replay_archive(path, *, session_id=None, component=None):
    """只读取已知日志条目。缺帧/脱敏/覆盖时仍检查可用帧，并标记不完整。"""
    if component is not None and component not in _COMPONENTS:
        raise ValueError('未知回放组件')
    report = dict(replay_version=1, tool_version=__version__, status='unsupported', complete=False,
                  frames=0, matched=0, mismatched=0, skipped=0, diagnostics=0,
                  components={}, issues=[], details=[])
    issue_counts = {}

    def issue(code):
        issue_counts[code] = issue_counts.get(code, 0) + 1

    def skipped(row,code,reason):
        issue(code)
        report['skipped']+=1
        if len(report['details'])<_MAX_DETAILS:
            report['details'].append(dict(component=row.get('component'),operation=row.get('operation'),
                sequence=row.get('sequence'),status='incomplete',reason=reason[:128] if isinstance(reason,str) else code))

    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(infos) > 32 or len(set(names)) != len(names):
            raise ValueError('诊断ZIP存在过多或重复条目')
        if sum(info.file_size for info in infos) > _MAX_ARCHIVE_BYTES:
            raise ValueError('诊断ZIP解压数据超过安全读取上限')
        if 'summary.json' not in names or archive.getinfo('summary.json').file_size > 65536:
            raise ValueError('缺少有效的诊断摘要')
        try:summary = _json(archive.read('summary.json'))
        except (ValueError, UnicodeError, RecursionError) as error:
            raise ValueError('诊断摘要无法解析') from error
        if not isinstance(summary, dict):raise ValueError('诊断摘要结构无效')
        report['recorded_version'] = summary.get('version')
        report['session_id'] = session_id or summary.get('session_id')
        report['source_schema'] = summary.get('schema', 1)
        report['recording_epoch'] = summary.get('recording_epoch')
        for key in ('dropped', 'overwritten', 'truncated', 'oversized', 'payload_redacted',
                    'payload_invalid', 'export_rejected', 'incomplete_frames', 'incomplete'):
            value = summary.get(key, 0)
            if isinstance(value, (int, float)) and value > 0:issue(key)
        if summary.get('write_error'):issue('write_error')
        if summary.get('version') and summary['version'] != __version__:
            report['version_note'] = '记录版本与回放版本不同；差异可能来自代码变更'

        last_sequence = None
        epoch_starts, frame_epochs = set(), set()
        if report['session_id'] != summary.get('session_id'):
            issue('historical_session_integrity_unknown')
        for name in _LOGS:
            if name not in names:continue
            with archive.open(name) as stream:
                while True:
                    line = stream.readline(_MAX_LINE_BYTES + 1)
                    if not line:break
                    if len(line) > _MAX_LINE_BYTES:
                        issue('oversized_line')
                        while line and not line.endswith(b'\n'):
                            line = stream.readline(_MAX_LINE_BYTES + 1)
                        continue
                    if not line.strip():continue
                    try:row = _json(line)
                    except (ValueError, UnicodeError, RecursionError):
                        issue('invalid_json');continue
                    if not isinstance(row, dict):
                        issue('invalid_row');continue
                    if row.get('session_id') != report['session_id']:continue
                    if row.get('event') == 'diagnostic.start' and isinstance(row.get('recording_epoch'), str):
                        epoch_starts.add(row['recording_epoch'])
                    if row.get('event') != 'replay.frame':
                        report['diagnostics'] += 1
                        continue
                    if component is not None and row.get('component') != component:continue
                    report['frames'] += 1
                    if isinstance(row.get('recording_epoch'), str):
                        frame_epochs.add(row['recording_epoch'])
                    elif summary.get('recording_epoch'):
                        issue('missing_recording_epoch')
                    if report['frames'] > _MAX_FRAMES:
                        raise ValueError('回放帧数超过安全上限')
                    sequence = row.get('sequence')
                    if type(sequence) is not int or sequence <= 0:
                        issue('missing_sequence')
                    elif last_sequence is not None and sequence <= last_sequence:
                        issue('non_increasing_sequence')
                    if type(sequence) is int:last_sequence = sequence
                    if (row.get('component') not in _COMPONENTS or not isinstance(row.get('payload'), dict)
                            or not isinstance(row.get('operation'), str)):
                        skipped(row,'invalid_frame','invalid_frame');continue
                    if any(row.get(key) for key in ('payload_truncated', 'payload_redacted', 'payload_invalid')):
                        skipped(row,'modified_payload','截断、脱敏或非法数据影响本帧');continue
                    if row['payload'].get('complete') is False or row['payload'].get('incomplete'):
                        skipped(row,'partial_frame',row['payload'].get('reason') or row['payload'].get('incomplete') or 'partial_frame');continue
                    try:
                        result = _replay_frame(row)
                        if not isinstance(result, dict):raise ValueError('invalid replay result')
                    except Exception as error:
                        # 日志不可信，解析失败只报告类型，不执行额外回退或打印原始内容。
                        result = dict(status='invalid', error_type=type(error).__name__)
                    status = result.get('status')
                    matched = result.get('matched', status == 'matched')
                    if status in ('invalid', 'incomplete', 'unsupported'):
                        issue('unreplayable_frame');report['skipped'] += 1
                    elif matched is True:
                        report['matched'] += 1
                    else:
                        report['mismatched'] += 1
                    counts = report['components'].setdefault(row['component'], dict(frames=0, matched=0, mismatched=0))
                    counts['frames'] += 1
                    if matched is True:counts['matched'] += 1
                    elif status not in ('invalid', 'incomplete', 'unsupported'):counts['mismatched'] += 1
                    if len(report['details']) < _MAX_DETAILS:
                        detail = dict(component=row['component'], operation=row['operation'], sequence=sequence,
                                      status=status or ('matched' if matched else 'mismatch'))
                        for field in ('mismatches', 'reason', 'error_type', 'outcome', 'message_id', 'job_id'):
                            if field in result:detail[field] = result[field]
                        report['details'].append(detail)

    for _ in frame_epochs - epoch_starts:issue('missing_recording_start')
    report['issues'] = [dict(code=code, count=count) for code, count in sorted(issue_counts.items())]
    report['complete'] = bool(report['frames']) and not issue_counts
    report['status'] = ('mismatch' if report['mismatched'] else 'incomplete' if issue_counts else
                        'matched' if report['frames'] else 'unsupported')
    report['scope'] = '重放业务判断；游戏/Windows/云端返回为录制输入，不重新执行外部操作'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path, help='回放报告JSON路径')
    parser.add_argument('--session', help='选择运行会话，默认使用导出摘要的会话')
    parser.add_argument('--component', choices=sorted(_COMPONENTS))
    args = parser.parse_args(argv)
    try:report = replay_archive(args.archive, session_id=args.session, component=args.component)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(json.dumps(dict(status='invalid_archive', error_type=type(error).__name__), ensure_ascii=False))
        return 2
    destination = args.output or args.archive.with_name(args.archive.stem + '-回放报告.json')
    if destination.resolve() == args.archive.resolve():
        print(json.dumps(dict(status='invalid_output', reason='报告不能覆盖输入ZIP'),ensure_ascii=False))
        return 2
    try:destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        print(json.dumps(dict(status='write_failed'),ensure_ascii=False))
        return 2
    print(json.dumps(dict(report=str(destination), status=report['status'], frames=report['frames'],
                         matched=report['matched'], mismatched=report['mismatched']), ensure_ascii=False))
    return 1 if report['mismatched'] else 0


if __name__ == '__main__':raise SystemExit(main())
