"""Record guard observations and the bounded operands actually used by a codec."""

from .acquisition.model_codecs import (DECODER_SALT_POINTER_RVAS, SUPPORTED_DECODERS,
                                      decode_pointer)


def _result(operation, values):
    from .acquisition.talent_source import _Unknown, _validate_guards, _validate_model_decoder
    try:
        if operation == 'guards':_validate_guards(values['api'],values['talent'],values['decoder'])
        elif operation == 'model_decoder':_validate_model_decoder(values['decoder'])
        else:raise ValueError('unknown guard operation')
        return dict(outcome='accepted',reason='')
    except _Unknown as error:
        return dict(outcome='rejected',reason=str(error))


def record_guard(memory, operation, **values):
    # 更新本次读取摘要，即使未开启日志也保留即时失败解释，不做额外进程查询。
    try:
        info=getattr(memory,'diagnostic_info',None)
        if info is not None:
            info.update(decoder_mode=values['decoder'],
                        supported_decoders=','.join(map(str, SUPPORTED_DECODERS)),
                        guard_stage=operation)
            if 'api' in values:info.update(api_active=values['api'],talent_active=values['talent'])
        recorder=getattr(memory,'diagnostics',None)
        if recorder is None:return
        enabled=getattr(recorder,'snapshot',None)
        if enabled is not None and not enabled().get('enabled'):return
        recorder.record('replay.frame',component='reader',operation=operation,
            source=getattr(memory,'diagnostic_source',''),payload=dict(schema=1,input=values,
                expected=_result(operation,values)))
    except Exception:
        pass


def record_model_decode(memory, decoder, low, high, keys, pointer, *, salt=None):
    """Use existing operands only; the frame does not perform additional reads."""
    try:
        recorder = getattr(memory, 'diagnostics', None)
        if recorder is None:
            return
        enabled = getattr(recorder, 'snapshot', None)
        if enabled is not None and not enabled().get('enabled'):
            return
        values = dict(decoder=decoder, encoded_low=low, encoded_high=high,
                      keys=[list(item) for item in keys])
        if salt is not None:
            values['salt'] = salt
        recorder.record('replay.frame', component='reader', operation='decode_pointer',
            source=getattr(memory, 'diagnostic_source', ''), payload=dict(schema=1,
                scope='pointer_arithmetic',
                input=values,
                expected=dict(outcome='computed', pointer=pointer)))
    except Exception:
        pass


def _replay_model_decode(payload):
    values = payload.get('input')
    required = {'decoder', 'encoded_low', 'encoded_high', 'keys'}
    if isinstance(values, dict) and type(values.get('decoder')) is int:
        if values['decoder'] in DECODER_SALT_POINTER_RVAS:
            required.add('salt')
    if not isinstance(values, dict) or set(values) != required:
        return dict(status='invalid', reason='invalid_codec_observations')
    keys = values['keys']
    if (type(keys) is not list or len(keys) != 2
            or any(type(item) is not list or len(item) != 2
                   or type(item[0]) is not int or not 0 <= item[0] < 4096
                   or type(item[1]) is not int or not 0 <= item[1] <= 0xffffffff
                   for item in keys)):
        return dict(status='invalid', reason='invalid_codec_keys')
    position = 0

    def read_key(index):
        nonlocal position
        if position >= len(keys) or keys[position][0] != index:
            raise ValueError('codec key sequence changed')
        value = keys[position][1]
        position += 1
        return value

    try:
        pointer = decode_pointer(values['decoder'], values['encoded_low'],
                                 values['encoded_high'], read_key, salt=values.get('salt'))
    except (ValueError, TypeError):
        return dict(status='mismatch', matched=False,
                    mismatches=['codec_inputs'], outcome='rejected',
                    reason='codec_inputs_changed')
    observed = dict(outcome='computed', pointer=pointer)
    matched = observed == payload.get('expected')
    return dict(status='matched' if matched else 'mismatch', matched=matched,
                mismatches=[] if matched else ['codec_result'], **observed)


def replay_reader_frame(operation,payload):
    if not isinstance(payload,dict) or payload.get('schema')!=1:
        return dict(status='invalid',reason='unsupported_reader_schema')
    if operation == 'decode_pointer':
        return _replay_model_decode(payload)
    values=payload.get('input')
    keys={'api','talent','decoder'} if operation=='guards' else {'decoder'} if operation=='model_decoder' else set()
    if (not keys or not isinstance(values,dict) or set(values)!=keys
            or any(type(value) is not int or not 0<=value<=0xffffffff for value in values.values())):
        return dict(status='invalid',reason='invalid_guard_observations')
    observed=_result(operation,values)
    matched=observed==payload.get('expected')
    return dict(status='matched' if matched else 'mismatch',matched=matched,
                mismatches=[] if matched else ['guard_result'],**observed)
