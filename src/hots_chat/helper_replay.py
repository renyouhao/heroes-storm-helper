"""Bounded JSON recording and offline replay of the actual pure helper sessions.

Only external callback/transport outcomes are simulated. No replay path constructs
a game reader, native backend, hook, worker, or sender. Each frame contains its
own prior state, so enabling diagnostics midway and log rotation remain useful.
"""
from dataclasses import fields, is_dataclass
from functools import lru_cache, wraps
import inspect
import json
import math


_MAX_FRAME = 1024 * 1024
_MAX_STRING = 16384
_UNSET = object()
_OPERATIONS = {
    'talent': frozenset(('configure', 'stop', 'poll')),
    'board': frozenset(('configure', 'stop', 'cancel', 'mouse', 'tick', 'dispatch', 'hit_test')),
}
_STATE = {
    'talent': ('config', 'status', '_generation', '_retired_generations', '_attempts', '_revision'),
    'board': ('config', 'status', 'double_click_seconds', 'double_click_size', '_first_click',
              '_pending', '_left_held', '_revision', '_token', '_attempts', '_last_attempt'),
}


class _Codec:
    def __init__(self):
        self.aliases = {}

    def alias(self, kind, value):
        if not value:
            return value
        pool = self.aliases.setdefault(kind, {})
        if value not in pool:
            if len(pool) >= 4096:
                raise ValueError('identity_limit')
            pool[value] = len(pool) + 1
        return pool[value]

    def encode(self, value, key='', depth=0):
        if depth > 24:
            raise ValueError('depth_limit')
        if key in ('pid', 'hwnd') and type(value) is int and value > 0:
            value = self.alias(key, value)
        elif key in ('generation', '_generation') and value:
            value = ('generation-' + str(self.alias('generation', value)),)
        elif key == 'match_token' and isinstance(value, str) and value:
            value = 'match-' + str(self.alias('match', value))
        if value is None or type(value) in (bool, int):
            return value
        if type(value) is float and math.isfinite(value):
            return value
        if type(value) is str:
            if len(value) > _MAX_STRING:
                raise ValueError('string_limit')
            return value
        if is_dataclass(value) and not isinstance(value, type):
            name = type(value).__name__
            if name not in _types():
                raise ValueError('unsupported_type')
            return {'type': name, 'fields': {_field_name(name, field.name): self.encode(
                getattr(value, field.name), field.name, depth + 1) for field in fields(value)}}
        if type(value) in (list, tuple):
            if len(value) > 4096:
                raise ValueError('collection_limit')
            names = [''] * len(value)
            if key == 'identity' and len(value) >= 3:
                names[:3] = ['pid', 'hwnd', 'match_token']
            elif key == '_first_click' and value:
                names[0] = 'identity'
            elif key == '_retired_generations':
                names = ['_generation'] * len(value)
            return {'type': 'tuple' if type(value) is tuple else 'list', 'items': [
                self.encode(item, names[index], depth + 1) for index, item in enumerate(value)]}
        if type(value) is dict:
            if len(value) > 4096:
                raise ValueError('collection_limit')
            return {'type': 'dict', 'items': [[
                self.encode(k, 'identity' if type(k) is tuple and key == '_attempts' else '', depth + 1),
                self.encode(v, k if isinstance(k, str) else '', depth + 1)]
                for k, v in value.items()]}
        raise ValueError('unsupported_value')


@lru_cache(maxsize=1)
def _types():
    # Lazy imports break the decorator/session import cycle. These modules do not
    # initialize Win32 APIs; only explicitly whitelisted data classes are decoded.
    from .acquisition.talent_source import TalentChoice, TalentSnapshot, TalentTier
    from .acquisition.board_source import BoardSnapshot
    from .loot_native import GameTarget
    from .talent import TalentConfig, TalentStatus, _Attempt
    from .talent_broadcast import (BroadcastSendResult, DispatchAction, HitSnapshot, InputContext,
        StatHitSnapshot, TalentBroadcastConfig, TalentBroadcastStatus, _Pending)
    return {cls.__name__: cls for cls in (BoardSnapshot, TalentChoice, TalentSnapshot, TalentTier, GameTarget,
        TalentConfig, TalentStatus, _Attempt, BroadcastSendResult, DispatchAction, HitSnapshot,
        InputContext, StatHitSnapshot, TalentBroadcastConfig, TalentBroadcastStatus, _Pending)}


def _field_name(class_name, field_name):
    # This is a harmless action counter, not an authentication credential. Keep
    # the recorder's generic credential-key filter intact for real tokens.
    return 'action_token' if (class_name, field_name) == ('DispatchAction', 'token') else field_name


def _decode(value, depth=0):
    if depth > 24:
        raise ValueError('depth_limit')
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is str and len(value) <= _MAX_STRING:
        return value
    if type(value) is not dict:
        raise ValueError('invalid_typed_value')
    kind = value.get('type')
    if kind in ('tuple', 'list', 'dict'):
        items = value.get('items')
        if type(items) is not list or len(items) > 4096 or set(value) != {'type', 'items'}:
            raise ValueError('invalid_collection')
        if kind == 'dict':
            if any(type(pair) is not list or len(pair) != 2 for pair in items):
                raise ValueError('invalid_dictionary')
            return {_decode(k, depth + 1): _decode(v, depth + 1) for k, v in items}
        result = [_decode(item, depth + 1) for item in items]
        return tuple(result) if kind == 'tuple' else result
    cls = _types().get(kind)
    data = value.get('fields')
    if (cls is None or set(value) != {'type', 'fields'} or type(data) is not dict
            or set(data) != {_field_name(kind, field.name) for field in fields(cls)}):
        raise ValueError('invalid_data_class')
    return cls(**{field.name: _decode(data[_field_name(kind, field.name)], depth + 1)
                  for field in fields(cls)})


def _state(session, component):
    return {key: getattr(session, key) for key in _STATE[component]}


def _enabled(recorder):
    if recorder is None:
        return False
    try:
        snapshot = getattr(recorder, 'snapshot', None)
        return snapshot is None or bool(snapshot().get('enabled'))
    except Exception:
        return False


class _Capture:
    def __init__(self, session, component, codec):
        self.session, self.component, self.codec = session, component, codec
        self.events = []
        self.current = self.events
        self.incomplete = False

    def encode(self, value):
        try:
            return self.codec.encode(value)
        except Exception:
            self.incomplete = True
            return None

    def state(self):
        return self.encode(_state(self.session, self.component))

    def invoke(self, kind, function, *, value=_UNSET):
        event = {'kind': kind, 'before': self.state(), 'children': []}
        if value is not _UNSET:
            event['input'] = self.encode(value)
        self.current.append(event)
        previous, self.current = self.current, event['children']
        try:
            result = function()
            event['result'] = self.encode(result)
            return result
        except Exception as error:
            event['error'] = type(error).__name__
            raise
        finally:
            event['after'] = self.state()
            self.current = previous

    def transport(self, kind, function, value, options):
        callbacks = {key: (lambda key=key, callback=callback: self.invoke(
            kind + '.' + key, callback)) for key, callback in options.items()}
        return self.invoke(kind, lambda: function(value, **callbacks), value=value)


def invoke_talent_backend(session, target, options):
    """Record the existing backend call, preserving every original fence call."""
    capture = getattr(session, '_replay_capture', None)
    if capture is None:
        return session.backend.select_favorite(target, **options)
    return capture.transport('backend', session.backend.select_favorite, target, options)


def recorded_operation(component):
    """Optional recorder hook; disabled recording never serializes runtime data."""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def call(session, *args, **kwargs):
            recorder = getattr(session, 'diagnostics', None)
            if getattr(session, '_replay_capture', None) is not None or not _enabled(recorder):
                return function(session, *args, **kwargs)
            try:
                bound = signature.bind(session, *args, **kwargs)
                bound.apply_defaults()
                values = dict(bound.arguments)
                values.pop('self')
                codec = getattr(session, '_replay_codec', None)
                if codec is None:
                    codec = session._replay_codec = _Codec()
                capture = _Capture(session, component, codec)
                before = capture.state()
                if capture.incomplete:
                    raise ValueError('unsupported_state')
                inputs = {key: value for key, value in values.items()
                          if key not in ('allowed', 'validate', 'sender')}
                if function.__name__ == 'dispatch':
                    inputs['pending_ref'] = (session._pending is not None
                        and values['action'] is session._pending.action)
                if 'validate' in values:
                    inputs['has_validate'] = values['validate'] is not None
                inputs = codec.encode(inputs)
            except Exception:
                _record_incomplete(recorder, component, function.__name__, 'unsupported_input')
                return function(session, *args, **kwargs)
            for key in ('allowed', 'validate'):
                callback = values.get(key)
                if callback is not None:
                    bound.arguments[key] = lambda callback=callback, key=key: capture.invoke(key, callback)
            if 'sender' in values:
                bound.arguments['sender'] = lambda action, **options: capture.transport(
                    'sender', values['sender'], action, options)
            session._replay_capture = capture
            expected = {}
            try:
                result = function(*bound.args, **bound.kwargs)
                try:
                    expected['result'] = codec.encode(result)
                except Exception:
                    expected['incomplete'] = 'unsupported_result'
                return result
            except Exception as error:
                expected['error'] = type(error).__name__
                raise
            finally:
                session._replay_capture = None
                try:
                    expected['after'] = capture.state()
                    payload = dict(schema=1, before=before, input=inputs,
                                   boundaries=capture.events, expected=expected)
                    if capture.incomplete:
                        payload['incomplete'] = 'unsupported_boundary'
                    if len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > _MAX_FRAME:
                        _record_incomplete(recorder, component, function.__name__, 'size_limit')
                    else:
                        recorder.record('replay.frame', component=component,
                            operation=function.__name__, payload=payload)
                except Exception:
                    pass  # No diagnostic failure can change the actual helper outcome.
        return call
    return decorate


def _record_incomplete(recorder, component, operation, reason):
    try:
        recorder.record('replay.frame', component=component, operation=operation,
                        payload={'schema': 1, 'incomplete': reason})
    except Exception:
        pass


def record_board_hit_test(recorder, snapshot, point, context, result, *, session=None):
    """Capture the candidate rectangles even when service safety checks stop early."""
    if not _enabled(recorder):
        return
    try:
        codec = getattr(session, '_replay_codec', None)
        if codec is None:
            codec = _Codec()
            if session is not None:
                session._replay_codec = codec
        payload = dict(schema=1, input=codec.encode(dict(
            snapshot=snapshot, point=point, context=context)),
            expected={'result': codec.encode(result)})
        if len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > _MAX_FRAME:
            _record_incomplete(recorder, 'board', 'hit_test', 'size_limit')
        else:
            recorder.record('replay.frame', component='board', operation='hit_test', payload=payload)
    except Exception:
        _record_incomplete(recorder, 'board', 'hit_test', 'unsupported_input')


class _Replay:
    def __init__(self, session, component, events):
        self.session, self.component = session, component
        self.current = list(events)
        self.mismatches = []

    def state(self):
        # Replay state already contains anonymous identifiers; encode without
        # aliasing again, otherwise different identifiers could collapse.
        return _plain_encode(_state(self.session, self.component))

    def check(self, label, actual, expected):
        if actual != expected:
            self.mismatches.append(label)

    def restore(self, encoded):
        state = _decode(encoded)
        if type(state) is not dict or set(state) != set(_STATE[self.component]):
            raise ValueError('invalid_state')
        for key, value in state.items():
            if getattr(self.session, key) != value:
                setattr(self.session, key, value)

    def invoke(self, kind, function=None):
        if not self.current:
            raise ValueError('missing_boundary:' + kind)
        event = self.current.pop(0)
        if event.get('kind') != kind:
            raise ValueError('boundary_order:' + kind)
        self.check(kind + '.before', self.state(), event['before'])
        previous, self.current = self.current, list(event['children'])
        try:
            if function is not None:
                try:
                    result = function()
                    self.check(kind + '.result', _plain_encode(result), event.get('result'))
                except Exception as error:
                    self.check(kind + '.error', type(error).__name__, event.get('error'))
                    raise
                self.check(kind + '.after', self.state(), event['after'])
                return result
            # External callback/transport may alter the session (disable, cancel,
            # revision change). Restore only actual changes to keep references.
            self.restore(event['after'])
            if 'error' in event:
                raise _safe_error(event['error'])
            return _decode(event['result'])
        finally:
            if self.current:
                self.mismatches.append(kind + '.unconsumed_boundaries')
            self.current = previous

    def transport(self, kind, _unused_function, value, options):
        if not self.current or self.current[0].get('kind') != kind:
            raise ValueError('missing_transport:' + kind)
        transport = self.current[0]
        self.check(kind + '.input', _plain_encode(value), transport.get('input'))

        def simulated():
            while self.current:
                child = self.current[0]
                name = child['kind'].removeprefix(kind + '.')
                if name not in options:
                    raise ValueError('unexpected_transport_callback')
                # State changes between callbacks belong to the external
                # transport. The callback itself still executes real can_send.
                self.restore(child['before'])
                self.invoke(child['kind'], options[name])
            self.restore(transport['after'])
            if 'error' in transport:
                raise _safe_error(transport['error'])
            return _decode(transport['result'])
        return self.invoke(kind, simulated)


def _plain_encode(value):
    # Replay state already has anonymous identifiers. Do not prefix/alias again.
    return _encode_plain(value)


def _encode_plain(value):
    if value is None or type(value) in (bool, int, float, str):
        return value
    if is_dataclass(value):
        name = type(value).__name__
        return {'type': name, 'fields': {
            _field_name(name, field.name): _encode_plain(getattr(value, field.name)) for field in fields(value)}}
    if type(value) in (tuple, list):
        return {'type': 'tuple' if type(value) is tuple else 'list',
                'items': [_encode_plain(item) for item in value]}
    if type(value) is dict:
        return {'type': 'dict', 'items': [[_encode_plain(k), _encode_plain(v)] for k, v in value.items()]}
    raise ValueError('unsupported_replay_value')


def _safe_error(name):
    return {'OSError': OSError, 'ValueError': ValueError, 'TypeError': TypeError,
            'RuntimeError': RuntimeError, 'PermissionError': PermissionError,
            'FileNotFoundError': FileNotFoundError, 'TimeoutError': TimeoutError,
            'KeyError': KeyError, 'AssertionError': AssertionError,
            'LookupError': LookupError}.get(name, RuntimeError)('recorded boundary failure')


def replay_helper_frame(component, operation, payload):
    """Replay one self-contained frame; malformed data never executes code."""
    report = {'matched': False, 'status': 'invalid', 'mismatches': []}
    try:
        if component not in _OPERATIONS or operation not in _OPERATIONS[component]:
            raise ValueError('unsupported_operation')
        if (type(payload) is not dict or payload.get('schema') != 1
                or len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > _MAX_FRAME):
            raise ValueError('unsupported_schema_or_size')
        if payload.get('incomplete'):
            report['status'] = 'incomplete'
            raise ValueError('incomplete_frame')
        if component == 'board' and operation == 'hit_test':
            from .board_service import hit_test
            inputs = _decode(payload['input'])
            result = hit_test(inputs['snapshot'], *inputs['point'])
            matched = _plain_encode(result) == payload['expected']['result']
            return dict(matched=matched, status='matched' if matched else 'mismatch',
                        mismatches=[] if matched else ['result'])
        if component == 'talent':
            from .talent import TalentSession
            session = TalentSession(None)
        else:
            from .talent_broadcast import TalentBroadcastSession
            session = TalentBroadcastSession(double_click_ms=500, double_click_size=(4, 4))
        replay = _Replay(session, component, payload['boundaries'])
        replay.restore(payload['before'])
        inputs = _decode(payload['input'])
        if type(inputs) is not dict:
            raise ValueError('invalid_input')
        session._replay_capture = replay
        if operation == 'poll':
            inputs['allowed'] = lambda: replay.invoke('allowed')
            if inputs.pop('has_validate'):
                inputs['validate'] = lambda: replay.invoke('validate')
        if operation == 'dispatch':
            if inputs.pop('pending_ref'):
                inputs['action'] = session._pending.action
            inputs['allowed'] = lambda: replay.invoke('allowed')
            inputs['sender'] = lambda action, **options: replay.transport('sender', None, action, options)
        # Talent's transport hook supplies backend.select_favorite for recording,
        # while replay consumes the captured transport without calling a backend.
        if component == 'talent':
            class Backend:
                def select_favorite(self, *_args, **_kwargs):
                    raise AssertionError('replay must never invoke a backend')
            session.backend = Backend()
        expected = payload['expected']
        try:
            result = getattr(session, operation)(**inputs)
            replay.check('result', _plain_encode(result), expected.get('result'))
            if 'error' in expected:
                replay.mismatches.append('expected_error')
        except Exception as error:
            replay.check('error', type(error).__name__, expected.get('error'))
        replay.check('after', replay.state(), expected['after'])
        if replay.current:
            replay.mismatches.append('unconsumed_boundaries')
        if expected.get('incomplete'):
            replay.mismatches.append('incomplete_result')
        report.update(matched=not replay.mismatches,
                      status='matched' if not replay.mismatches else 'mismatch',
                      mismatches=replay.mismatches, outcome=session.status.state,
                      reason=session.status.detail)
    except Exception as error:
        report['mismatches'].append('invalid_frame:' + type(error).__name__)
    return report
