"""Bounded translation decision recordings and offline replay of real controller methods.

Each frame is a self-contained checkpoint. Cloud replies and local phrase lookups
are external inputs; replay never constructs a real engine, source or worker.
"""
from collections import OrderedDict, deque
from dataclasses import fields, is_dataclass
from functools import wraps
import inspect
import math
import queue
import threading

from .cache import BoundedTranslationCache
from .chat_text import normalize_chat_text
from .context import LocalContext
from .models import AppEvent, ChatEvent, TranslationJob, TranslationResult
from .scheduler import PendingQueue
from .spam import AutomaticGuard, _SenderWindow


MAX_FRAME_BYTES = 128 * 1024


class CaptureTooLarge(ValueError):
    pass


class _Budget:
    def __init__(self):
        self.left = MAX_FRAME_BYTES
        self.nodes = 20000

    def copy(self, value, depth=0):
        self.nodes -= 1
        self.left -= 16
        if depth > 18 or self.left < 0 or self.nodes < 0:
            raise CaptureTooLarge('translation_state_budget')
        if value is None or type(value) in (bool, int):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError('non_finite_value')
            return value
        if isinstance(value, str):
            self.left -= len(value.encode('utf-8'))
            if self.left < 0 or len(value) > 16384:
                raise CaptureTooLarge('translation_text_budget')
            return value
        if is_dataclass(value):
            if isinstance(value, AppEvent) and value.kind == 'error' and isinstance(value.payload, dict):
                value = AppEvent(value.kind, value.generation, {key: item for key, item in value.payload.items()
                    if key in ('message_id', 'job_id', 'error')})
            elif isinstance(value, AppEvent) and value.kind in ('generation', 'capture_state', 'error', 'conflict') and not isinstance(value.payload, dict):
                value = AppEvent(value.kind, value.generation, None)
            elif isinstance(value, AppEvent) and value.kind in ('generation', 'capture_state', 'conflict'):
                value = AppEvent(value.kind, value.generation, None)
            if isinstance(value, ChatEvent):
                sender = value.sender
                if not (sender.startswith('sender:') and len(sender) == 71):
                    sender = 'sender:' + AutomaticGuard._identity(value.channel, sender).hex()
                value = ChatEvent(value.message_id, value.generation, value.channel, sender, value.timestamp, value.text)
            value = {field.name: getattr(value, field.name) for field in fields(value)}
        if isinstance(value, dict):
            return {str(key): self.copy('<translation-error>' if key == 'error' and isinstance(item, str) else item, depth + 1)
                    for key, item in value.items()}
        if isinstance(value, (tuple, list, deque, set, frozenset)):
            if isinstance(value, (set, frozenset)):
                value = sorted(value)
            return [self.copy(item, depth + 1) for item in value]
        raise ValueError('unsupported_translation_value')


def _scope(controller, args, operation):
    texts, ids, senders = set(), set(), set()

    def visit(value):
        if isinstance(value, ChatEvent):
            texts.update((value.text, normalize_chat_text(value.text)))
            ids.add(value.message_id)
            senders.add(AutomaticGuard._identity(value.channel, value.sender).hex())
        elif isinstance(value, TranslationJob):
            texts.add(value.text)
        elif isinstance(value, AppEvent):
            if value.kind == 'chat':
                visit(value.payload)
        elif isinstance(value, str):
            texts.add(value)
            if value in controller.messages:
                visit(controller.messages[value])
        elif isinstance(value, (tuple, list)):
            for item in value:
                visit(item)

    visit(args)
    cache_key = None
    event = None
    if operation == 'on_chat':
        event = args[0]
    elif operation == 'source_event' and args[0].kind == 'chat':
        event = args[0].payload
    elif operation == 'retry_message':
        event = controller.messages.get(args[0])
    if event is not None:
        from .controller import incoming_source_language
        text = normalize_chat_text(event.text)
        cache_key = [controller.engine.provider_id, controller.engine.config_version,
                     text, incoming_source_language(text), 'zh']
    elif operation == 'submit_manual':
        cache_key = [controller.engine.provider_id, controller.engine.config_version, args[0], 'zh', args[1]]
    elif operation == 'cloud_complete':
        cache_key = list(args[0][1:])
    return {'texts': sorted(texts), 'message_ids': sorted(ids), 'senders': sorted(senders), 'cache_key': cache_key}


def snapshot_state(controller, scope, budget=None):
    """Project only cache/message/sender entries that this operation can consult.

    The complete queue/subscriber and character-rate windows are preserved. We
    deliberately do not copy the 4 MiB cache, 500 chat messages or credentials.
    """
    budget = budget or _Budget()
    state = {
        'generation': controller.generation, 'paused': controller.paused,
        'automatic_paused': controller.automatic_paused, 'stopped': controller.stopped,
        'configuration_held': controller.configuration_held,
        'cloud_configured': getattr(controller, 'cloud_configured', True),
        'allowed_channels': sorted(controller.allowed_channels),
        'provider_id': controller.engine.provider_id, 'config_version': controller.engine.config_version,
        'active': controller.active,
        'messages': [event for key, event in controller.messages.items() if key in scope['message_ids']],
        'pending': {'capacity': controller.pending.capacity, 'automatic_capacity': controller.pending.automatic_capacity,
                    'ttl': controller.pending.ttl, 'items': list(controller.pending.items),
                    'deadlines': [[key, value] for key, value in controller.pending.deadlines.items()]},
        'subscribers': [[key, jobs] for key, jobs in controller.subscribers.items()],
        'cache': {'max_items': controller.cache.max_items, 'max_bytes': controller.cache.max_bytes,
                  'items': [[key, result] for key, (result, _) in controller.cache.items.items()
                            if list(key) == scope['cache_key']]},
        'guard': {'sender_limit': controller.guard.sender_limit, 'sender_seconds': controller.guard.sender_seconds,
                  'character_limit': controller.guard.character_limit, 'character_seconds': controller.guard.character_seconds,
                  'max_senders': controller.guard.max_senders,
                  'characters': list(controller.guard.characters), 'character_total': controller.guard.character_total,
                  'senders': [[key.hex(), window.updated_at, list(window.entries)]
                              for key, window in controller.guard.senders.items() if key.hex() in scope['senders']]},
    }
    return budget.copy(state)


def _enabled(controller):
    try:
        return controller.diagnostics is not None and bool(controller.diagnostics.snapshot().get('enabled'))
    except Exception:
        return False


def record_operation(name):
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def wrapped(controller, *args, **kwargs):
            # No capture or additional clock calls when diagnostics is disabled.
            if not _enabled(controller):
                return function(controller, *args, **kwargs)
            with controller.condition:
                # Only the lock owner can be nested in the current recording.
                # Another thread must wait and then create its own frame.
                if getattr(controller, '_replay_capture', None) is not None:
                    return function(controller, *args, **kwargs)
                capture = {'schema': 1, 'operation': name, 'complete': True,
                           'external': {'job_ids': [], 'local_results': [], 'times': []}, 'events': []}
                budget = _Budget()
                try:
                    bound = signature.bind(controller, *args, **kwargs)
                    bound.apply_defaults()
                    canonical_args = tuple(value for key, value in bound.arguments.items() if key != 'self')
                    scope_args = canonical_args[:3] if name == 'cloud_complete' else canonical_args
                    scope = _scope(controller, scope_args, name)
                    capture['scope'] = budget.copy(scope)
                    capture['input'] = budget.copy({'args': canonical_args, 'kwargs': {}})
                    if name == 'cloud_complete' and canonical_args[3]:
                        capture['input']['args'][3] = '<translation-error>'
                    capture['before'] = snapshot_state(controller, scope, budget)
                except Exception:
                    capture.update(complete=False, reason='translation_state_budget_or_capture_error')
                controller._replay_capture = capture
                error = None
                result = None
                try:
                    result = function(controller, *args, **kwargs)
                    return result
                except Exception as failure:
                    error = {'type': type(failure).__name__, 'message': '<exception-message>'}
                    raise
                finally:
                    controller._replay_capture = None
                    try:
                        if capture['complete']:
                            capture['expected'] = {'events': budget.copy(capture.pop('events')),
                                                   'after': snapshot_state(controller, scope, budget), 'error': error,
                                                   'return': budget.copy(result)}
                            capture['external'] = budget.copy(capture['external'])
                        else:
                            capture = {'schema': 1, 'operation': name, 'complete': False, 'reason': capture['reason'],
                                       **({'input': capture['input']} if 'input' in capture else {})}
                        controller.diagnostics.record('replay.frame', component='translation', operation=name, payload=capture)
                    except Exception:
                        # A frame must never break a translation operation.
                        try:
                            controller.diagnostics.record('replay.frame', component='translation', operation=name,
                                payload={'schema': 1, 'operation': name, 'complete': False, 'reason': 'translation_state_budget_or_capture_error'})
                        except Exception:
                            pass
        return wrapped
    return decorate


def capture_event(controller, event):
    capture = getattr(controller, '_replay_capture', None)
    if capture is not None and capture['complete']:
        if len(capture['events']) >= 2000:
            capture.update(complete=False, reason='translation_event_budget')
        else:
            capture['events'].append(event)


class _OfflineEngine:
    def __init__(self, provider_id, config_version):
        self.provider_id, self.config_version = provider_id, config_version

    def translate(self, job):
        raise RuntimeError('offline_replay_cannot_translate')


def _restore(state, external):
    from .controller import ChatController
    controller = ChatController.__new__(ChatController)
    controller.diagnostics = None
    controller._replay_capture = None
    controller.condition = threading.Condition(threading.RLock())
    controller.engine = _OfflineEngine(state['provider_id'], state['config_version'])
    controller.account = None
    for field in ('generation', 'paused', 'automatic_paused', 'stopped', 'configuration_held', 'cloud_configured'):
        setattr(controller, field, state[field])
    controller.allowed_channels = set(state['allowed_channels'])
    controller.active = tuple(state['active']) if state['active'] is not None else None
    controller.messages = OrderedDict((row['message_id'], ChatEvent(**row)) for row in state['messages'])
    controller.context = LocalContext()
    controller.events = queue.Queue(1000)
    controller.source_events = queue.Queue(1000)
    controller.overflow = False
    times = iter(external['times'])
    controller._monotonic_source = lambda: next(times)
    controller.pending = PendingQueue(monotonic=controller._replay_monotonic, **{key: state['pending'][key] for key in ('capacity', 'automatic_capacity', 'ttl')})
    controller.pending.items.extend((tuple(key), manual) for key, manual in state['pending']['items'])
    controller.pending.deadlines = {tuple(key): deadline for key, deadline in state['pending']['deadlines']}
    controller.subscribers = {tuple(key): [TranslationJob(**job) for job in jobs] for key, jobs in state['subscribers']}
    controller.cache = BoundedTranslationCache(state['cache']['max_items'], state['cache']['max_bytes'])
    for key, result in state['cache']['items']:
        controller.cache.put(tuple(key), TranslationResult(**result))
    guard = state['guard']
    controller.guard = AutomaticGuard(monotonic=controller._replay_monotonic,
        **{key: guard[key] for key in ('sender_limit', 'sender_seconds', 'character_limit', 'character_seconds', 'max_senders')})
    controller.guard.characters = deque(tuple(row) for row in guard['characters'])
    controller.guard.character_total = guard['character_total']
    controller.guard.senders = OrderedDict((bytes.fromhex(key), _SenderWindow(updated, deque(entries, maxlen=guard['sender_limit'])))
                                           for key, updated, entries in guard['senders'])
    controller.guard._identity = lambda channel, sender: bytes.fromhex(sender[7:]) if sender.startswith('sender:') else AutomaticGuard._identity(channel, sender)
    ids = iter(external['job_ids'])
    controller._job_id_factory = lambda: next(ids)
    local_results = iter(external['local_results'])
    controller.local_resolver = lambda text, target: next(local_results)
    return controller


_OPERATIONS = frozenset(('on_chat', 'source_event', 'submit_manual', 'retry_message', 'hold_configuration',
    'pause_translation', 'pause_automatic', 'set_channels', 'dispatch', 'cloud_authorize', 'cloud_complete'))


def _outcome(operation, events, result, error):
    if error is not None:
        return {'outcome': 'rejected', 'reason': 'controller_validation'}
    if not events:
        if operation == 'cloud_authorize':
            return {'outcome': 'authorized' if result else 'cancelled', 'reason': 'request_gate'}
        return {'outcome': 'no_output', 'reason': 'no_ui_event'}
    event = events[-1]
    detail = event['payload'] if isinstance(event['payload'], dict) else {}
    kind = event['kind']
    outcome = detail.get('state', kind) if kind == 'translation_state' else kind
    report = {'outcome': outcome}
    for key in ('message_id', 'job_id'):
        if isinstance(detail.get(key), str):
            report[key] = detail[key]
    reason = detail.get('reason', '')
    known = {'翻译服务未配置': 'cloud_unconfigured', '配置处理中': 'configuration_held',
             '云翻译已关闭': 'cloud_paused', '自动翻译已暂停': 'automatic_paused',
             '不支持的语言或符号': 'unsupported_language',
             'sender_rate': 'sender_rate', 'global_characters': 'global_characters',
             'queue_full': 'queue_full', 'expired': 'expired'}
    if reason in known:
        report['reason'] = known[reason]
    elif kind == 'error':
        report['reason'] = 'cloud_error' if operation == 'cloud_complete' else 'controller_rejection'
    elif kind in ('result', 'manual_result'):
        report['reason'] = {'缓存': 'cache', '已校对本机短句': 'local'}.get(detail.get('provenance'), 'cloud')
    return report


def replay_translation_frame(payload):
    report = {'component': 'translation', 'operation': payload.get('operation') if isinstance(payload, dict) else None}
    if not isinstance(payload, dict) or payload.get('schema') != 1:
        return dict(report, status='invalid', reason='unsupported_translation_schema')
    if payload.get('complete') is False:
        return dict(report, status='incomplete', reason=payload.get('reason', 'partial_translation_frame'))
    operation = payload.get('operation')
    if operation not in _OPERATIONS or payload.get('complete') is not True:
        return dict(report, status='invalid', reason='invalid_translation_operation')
    try:
        # Validate structural bounds even when called directly without the ZIP reader.
        _Budget().copy(payload)
        controller = _restore(payload['before'], payload['external'])
        args = list(payload['input']['args'])
        kwargs = payload['input']['kwargs']
        if operation == 'on_chat':
            args[0] = ChatEvent(**args[0])
        elif operation == 'source_event':
            event = args[0]
            if event['kind'] == 'chat':
                event = dict(event, payload=ChatEvent(**event['payload']))
            args[0] = AppEvent(**event)
        elif operation == 'cloud_complete':
            args[0] = tuple(args[0])
            args[1] = TranslationJob(**args[1])
            args[2] = TranslationResult(**args[2]) if args[2] is not None else None
        method = {'source_event': 'handle_source_event', 'dispatch': '_dispatch_pending',
                  'cloud_authorize': '_cloud_authorized', 'cloud_complete': '_complete_translation'}.get(operation, operation)
        observed_events = []
        controller._emit = lambda event: observed_events.append(_Budget().copy(event))
        error = None
        result = None
        with controller.condition:
            try:
                result = getattr(controller, method)(*args, **kwargs)
            except (ValueError, RuntimeError) as failure:
                error = {'type': type(failure).__name__, 'message': '<exception-message>'}
        actual = {'events': observed_events, 'after': snapshot_state(controller, payload['scope']), 'error': error,
                  'return': _Budget().copy(result)}
        matches = actual == payload['expected']
        differences = [key for key in actual if actual[key] != payload['expected'].get(key)]
        return dict(report, status='matched' if matches else 'mismatch', differences=differences,
                    event_kinds=[event['kind'] for event in observed_events],
                    **_outcome(operation, observed_events, result, error))
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, StopIteration, OverflowError):
        return dict(report, status='invalid', reason='invalid_or_incomplete_translation_checkpoint')


def replay_translation_frames(rows):
    return [replay_translation_frame(row['payload'] if row.get('event') == 'replay.frame' else row)
            for row in rows if row.get('component', 'translation') == 'translation']
