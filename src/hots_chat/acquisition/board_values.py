"""原生 UI 数值的纯判定；离线回放复用，不访问游戏或窗口。"""
import re


def parse_quest_progress(raw_state, raw_progress, label_count, label_visible, label_text, stable):
    if (type(raw_state) is not int or type(raw_progress) is not int
            or not 0 <= raw_state <= 0xffffffff or not 0 <= raw_progress <= 0xffffffff
            or type(label_count) is not int or not 0 <= label_count <= 512
            or type(label_visible) is not bool or type(stable) is not bool
            or label_text is not None and (not isinstance(label_text, str) or len(label_text) > 80)):
        return dict(state='unknown', progress=None, reason='invalid_observations')
    result = lambda state, value, reason: dict(state=state, progress=value, reason=reason)
    if not stable:
        return result('unknown', None, 'changed_during_read')
    if raw_state == 0:
        return result('', None, 'no_quest')
    if raw_state == 2:
        return result('untracked', None, 'untracked')
    if raw_state == 3:
        return result('complete', None, 'completed')
    if raw_state not in (1, 4):
        return result('unknown', None, 'unknown_state')
    if label_count != 1:
        return result('unknown', None, 'missing_or_ambiguous_label')
    if not label_visible:
        return result('unknown', None, 'hidden_label')
    if (label_text is None
            or re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)', label_text) is None):
        return result('unknown', None, 'non_numeric_label')
    if int(label_text.replace(',', '')) != raw_progress:
        return result('unknown', None, 'value_mismatch')
    return result('known', raw_progress, 'verified')
