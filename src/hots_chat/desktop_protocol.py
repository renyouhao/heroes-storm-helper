"""Small, explicit IPC contract for the local desktop renderer.

Only allowlisted UI actions cross this boundary. Credentials and arbitrary
Python attributes are deliberately absent from the contract.
"""
from .channels import CHANNEL_PRESETS
from .repeat import validate_repeat_config
from .desktop_actions import ACTION_COMMANDS, validate_action, DesktopActionError


class ProtocolError(ValueError):
    """An input error whose message is safe to display to the renderer."""


_FIELDS = {
    'snapshot': (), 'shutdown': (), 'translate': ('text', 'target'),
    'draft': ('text',), 'copy': (), 'set_channel': ('value',),
    'set_auto': ('enabled',), 'set_language': ('target',), 'quick_input': (),
    'open_settings': (), 'check_updates': (), 'open_cloud': (),
    'loot_prepare': (), 'loot_cancel': (), 'repeat_prepare': (),
    'repeat_config': ('action', 'interval'), 'repeat_cancel': (),
    'talent_toggle': ('enabled',), 'board_toggle': ('enabled',),
    'helper_retry': ('source',), 'phrase_use': ('id',),
    'phrase_favorite': ('id',), 'phrase_edit': ('id',),
    'phrase_delete': ('id',), 'phrase_import': (), 'phrase_export': ('id',),
    'retry_message': ('id',), 'message_details': ('id',),
    'ui_prepare': (),
}
COMMANDS = frozenset(_FIELDS) | ACTION_COMMANDS


def validate_command(command, payload=None):
    """Return a fresh, validated payload; reject unknown actions and fields."""
    if isinstance(command, str) and command in ACTION_COMMANDS:
        try:
            return validate_action(command, payload)
        except DesktopActionError as error:
            raise ProtocolError(str(error)) from None
    if not isinstance(command, str) or command not in _FIELDS:
        raise ProtocolError('不支持的桌面操作。')
    payload = {} if payload is None else payload
    if not isinstance(payload, dict) or set(payload) - set(_FIELDS[command]):
        raise ProtocolError('操作参数无效。')
    required = set(_FIELDS[command]) - ({'id'} if command == 'phrase_edit' else set())
    if required - set(payload):
        raise ProtocolError('操作参数不完整。')
    result = dict(payload)
    if 'text' in result:
        from .chat_text import character_count
        text = result['text']
        if not isinstance(text, str) or character_count(text) > 1000:
            raise ProtocolError('请输入不超过 1000 个字符的内容。')
        if command == 'translate' and not text.strip():
            raise ProtocolError('请先输入要翻译的中文。')
    if 'target' in result and result['target'] not in ('en', 'ko'):
        raise ProtocolError('目标语言只能选择英语或韩语。')
    if 'enabled' in result and type(result['enabled']) is not bool:
        raise ProtocolError('开关值必须为布尔值。')
    if command == 'set_channel' and (not isinstance(result['value'], str)
                                     or result['value'] not in CHANNEL_PRESETS):
        raise ProtocolError('频道选择无效。')
    if command == 'repeat_config':
        try:
            validate_repeat_config(result['action'], result['interval'])
        except ValueError as error:
            raise ProtocolError(str(error)) from error
    if command == 'helper_retry' and result['source'] not in ('talent', 'board'):
        raise ProtocolError('辅助功能选择无效。')
    if 'id' in result and not (command == 'phrase_edit' and result['id'] is None):
        if not isinstance(result['id'], str) or not 1 <= len(result['id']) <= 1000:
            raise ProtocolError('记录编号无效。')
    return result


def validate_request(request):
    if not isinstance(request, dict) or set(request) - {'request_id', 'command', 'payload'}:
        raise ProtocolError('桌面请求格式无效。')
    request_id = request.get('request_id')
    if (type(request_id) not in (str, int) or isinstance(request_id, str)
            and not 1 <= len(request_id) <= 128):
        raise ProtocolError('桌面请求编号无效。')
    command = request.get('command')
    return request_id, command, validate_command(command, request.get('payload'))
