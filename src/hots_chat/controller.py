from collections import OrderedDict
from dataclasses import replace
import queue
import threading
import re
import time
import uuid
from .models import AppEvent,TranslationJob,TranslationResult
from .cache import BoundedTranslationCache
from .scheduler import PendingQueue
from .spam import AutomaticGuard
from .routing import incoming_source_language, incoming_translation_route
from .translation.base import TranslationError
from .clock import now as real_now
from .context import LocalContext
from .channels import SUPPORTED_CHANNELS
from .chat_text import character_count,normalize_chat_text
from .translation_replay import record_operation,capture_event


class ChatController:
    def __init__(self,source,engine,account,clock=None,monotonic=None,diagnostics=None):
        self.source,self.engine,self.account=source,engine,account
        self.diagnostics=diagnostics
        self.clock=clock or real_now
        self.generation=0; self.paused=False; self.automatic_paused=False; self.stopped=False
        self.configuration_held=False
        self.cache=BoundedTranslationCache()
        self.condition=threading.Condition(threading.RLock())
        self._replay_capture=None
        self._monotonic_source=monotonic or time.monotonic
        self._job_id_factory=lambda:uuid.uuid4().hex
        self.pending=PendingQueue(monotonic=self._replay_monotonic)
        self.guard=AutomaticGuard(monotonic=self._replay_monotonic)
        self.subscribers={}; self.active=None
        self.events=queue.Queue(1000); self.source_events=queue.Queue(1000)
        self.overflow=False
        self.messages=OrderedDict()
        self.context=LocalContext()
        self.local_resolver=lambda text,target:None
        self.allowed_channels=set(SUPPORTED_CHANNELS)
        self.thread=threading.Thread(target=self._work,name='translation-worker',daemon=True)
        self.thread.start()

    def _trace(self,event,**fields):
        if self.diagnostics is not None:
            fields.setdefault('generation',self.generation)
            try:self.diagnostics.record(event,**fields)
            except Exception:pass  # 诊断设施不能改变业务结果。

    def _replay_monotonic(self):
        value=self._monotonic_source()
        capture=self._replay_capture
        if capture is not None and capture['complete']:
            capture['external']['times'].append(value)
        return value

    def _new_job_id(self):
        value=self._job_id_factory()
        capture=self._replay_capture
        if capture is not None and capture['complete']:
            capture['external']['job_ids'].append(value)
        return value

    @property
    def idle(self):
        with self.condition: return not self.pending.items and self.active is None

    def _emit(self,event):
        try:self.events.put_nowait(event)
        except queue.Full:
            self.overflow=True
            capture=self._replay_capture
            if capture is not None:
                capture.update(complete=False,reason='output_queue_full')
            self._queue_overflow('output',self.events)
        else:capture_event(self,event)

    def _source_emit(self,event):
        try: self.source_events.put_nowait(event)
        except queue.Full:
            self.overflow=True
            self._queue_overflow('source',self.source_events)

    def _queue_overflow(self,name,event_queue):
        # Lost events cannot be reconstructed from decision snapshots.
        self._trace('replay.frame',component='translation',operation='queue_overflow',
                    payload={'schema':1,'operation':'queue_overflow','complete':False,
                             'reason':name+'_queue_full','queue':name,'capacity':event_queue.maxsize,
                             'observed_depth':event_queue.qsize(),'overflow':True})

    def start_receiving(self):
        if hasattr(self.source,'generation') and not self.source.running:
            self.source.generation=max(self.source.generation,self.generation)
        self.source.start(self._source_emit)

    def stop_receiving(self):
        self.source.stop()
        while True:
            try: self.source_events.get_nowait()
            except queue.Empty: break
        self.handle_source_event(AppEvent('generation',self.generation+1,'采集已停止。'))
        self._emit(AppEvent('capture_state',self.generation,'已停止'))

    @record_operation('source_event')
    def handle_source_event(self,event):
        with self.condition:
            if event.kind in ('generation','capture_state','error','conflict'):
                self._trace('capture.state',stage=event.kind,outcome='received',generation=event.generation)
            if event.kind=='generation':
                if event.generation<self.generation:return
                self.generation=event.generation
                for jobs in self.subscribers.values():
                    for job in jobs:
                        self._trace('translation.discarded',job_id=job.job_id,
                                    message_id=job.message_id,outcome='generation_change')
                # 清除等待任务和订阅；在途请求不向新一代发布。
                self.pending.clear(); self.subscribers.clear()
                # 重连/重新定位也会换代；本机防护窗口必须按单调时间自然过期。
                self.messages.clear()
                self.context.clear(self.generation)
            elif event.kind=='chat':
                self.on_chat(event.payload); return
            self._emit(event)

    @record_operation('on_chat')
    def on_chat(self,event):
        with self.condition:
            if event.generation != self.generation: return
            if event.channel not in self.allowed_channels:return
            text=normalize_chat_text(event.text)
            if text != event.text:event=replace(event,text=text)
            self.messages[event.message_id]=event
            self.context.add(event)
            while len(self.messages)>500: self.messages.popitem(last=False)
            self._emit(AppEvent('chat',event.generation,event))
            route=incoming_translation_route(event.text)
            self._trace('message.received',message_id=event.message_id,channel=event.channel,
                        characters=len(event.text),original=event.text)
            if route=='original_only':
                self._trace('translation.route',message_id=event.message_id,outcome='original_only')
                self._emit(AppEvent('translation_state',event.generation,
                           {'message_id':event.message_id,'state':'original_only','reason':''}))
                return
            source=route if route in ('en','ko') else None
            reason=('不支持的语言' if source is None else
                    '翻译服务未配置' if not getattr(self,'cloud_configured',True) else
                    '配置处理中' if self.configuration_held else
                    '云翻译已关闭' if self.paused else
                    '自动翻译已暂停' if self.automatic_paused else '')
            if reason:
                self._trace('translation.route',message_id=event.message_id,
                            outcome='unsupported' if source is None else 'paused')
                self._emit(AppEvent('translation_state',event.generation,
                           {'message_id':event.message_id,'state':'skipped','reason':reason}))
                return
            if len(event.text.encode('utf-8'))>4096:
                self._emit(AppEvent('error',event.generation,{'message_id':event.message_id,'error':'消息过长，仅显示原文。'})); return
            self._submit(event.text,source,'zh','auto',event.message_id)

    @record_operation('hold_configuration')
    def hold_configuration(self,held):
        with self.condition:
            self.configuration_held=bool(held)
            if held:self.pause_translation(True)
            self.condition.notify_all()

    @record_operation('pause_translation')
    def pause_translation(self,paused):
        with self.condition:
            held_resume=self.configuration_held and not paused
            self.paused=bool(paused) or self.configuration_held
            if self.paused:
                for key,_ in list(self.pending.items): self._drop(key,'云翻译已暂停。')
                self.pending.clear()
            self.condition.notify_all()
            status='配置处理中，云翻译保持暂停；继续接收原文。' if held_resume else (
                '云翻译已暂停；继续接收原文。' if self.paused else '云翻译已启用。')
            self._emit(AppEvent('status',self.generation,status))

    @record_operation('pause_automatic')
    def pause_automatic(self,paused):
        with self.condition:
            self.automatic_paused=paused
            if paused:
                retained=[]
                # A shared request can have both kinds of subscriber. Only
                # queued auto subscribers are cancelled; an active request
                # completes normally.
                for key,_ in list(self.pending.items):
                    manual=[]
                    for job in self.subscribers.get(key,[]):
                        if job.kind=='manual':manual.append(job)
                        else:
                            self._trace('translation.discarded',job_id=job.job_id,
                                        message_id=job.message_id,outcome='automatic_paused')
                            self._emit(AppEvent('error',job.generation,{'message_id':job.message_id,'job_id':job.job_id,'error':'自动翻译已暂停，待发请求已取消。'}))
                    if manual:
                        self.subscribers[key]=manual
                        retained.append((key,True))
                    else:self.subscribers.pop(key,None)
                self.pending.clear();self.pending.items.extend(retained)
            self.condition.notify_all()
            self._emit(AppEvent('status',self.generation,'自动翻译已暂停；继续接收原文。' if paused else '自动翻译已启用。'))

    def set_local_resolver(self,resolve): self.local_resolver=resolve

    def replace_engine(self,engine,account):
        with self.condition:
            if self.stopped:raise ValueError('助手已关闭。')
            if self.active is not None or self.pending.items:
                raise ValueError('请先暂停并等待当前云请求完成。')
            previous=self.engine
            self.engine,self.account=engine,account
            self.cache=BoundedTranslationCache()
        if previous is not engine:
            try:previous.close()
            except Exception:
                self._emit(AppEvent('status',self.generation,'翻译引擎已更换；旧引擎清理未完成。'))

    @record_operation('set_channels')
    def set_channels(self,channels):
        if not channels or set(channels)-SUPPORTED_CHANNELS:raise ValueError('频道选择无效。')
        with self.condition:self.allowed_channels=set(channels)

    @record_operation('submit_manual')
    def submit_manual(self,text,target):
        if target not in ('en','ko') or not text.strip() or character_count(text)>1000:
            raise ValueError('请输入 1–1000 个字符并选择英语或韩语。')
        with self.condition: return self._submit(text,'zh',target,'manual',None)

    @record_operation('retry_message')
    def retry_message(self,message_id):
        with self.condition:
            event=self.messages.get(message_id)
            if not event: raise ValueError('消息已过期。')
            if len(event.text.encode('utf-8'))>4096:
                raise ValueError('消息过长，仅显示原文。')
            source=incoming_source_language(event.text)
            if source is None: raise ValueError('目前仅支持英文和韩文，原文已保留。')
            return self._submit(event.text,source,'zh','auto',message_id,explicit=True)

    def _result(self,job,result,provenance='阿里云'):
        request_id=result.request_id
        self._trace('translation.result',job_id=job.job_id,message_id=job.message_id,
                    request_ref=result.job_id,
                    request_id=request_id if isinstance(request_id,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',request_id) else '',
                    elapsed_ms=result.elapsed_ms,translated=result.translated,
                    outcome={'缓存':'cache','已校对本机短句':'local'}.get(provenance,'cloud'))
        self._emit(AppEvent('manual_result' if job.kind=='manual' else 'result',job.generation,
                   {'message_id':job.message_id,'job_id':job.job_id,'translated':result.translated,
                    'engine_translation':result.translated,'detected_language':result.detected_language,
                    'provenance':provenance,'original':job.text,'target':job.target}))

    def _translation_state(self,job,state):
        if job.kind=='auto':
            self._emit(AppEvent('translation_state',job.generation,
                       {'message_id':job.message_id,'state':state,'reason':''}))

    def _submit(self,text,source,target,kind,message_id,explicit=False):
        if self.stopped: raise ValueError('助手已关闭。')
        job=TranslationJob(self._new_job_id(),message_id,self.generation,text,source,target,kind)
        self._trace('translation.submitted',job_id=job.job_id,message_id=message_id,
                    source=source,target=target,characters=len(text),original=text,stage=kind)
        if kind=='auto' and self.automatic_paused:
            self._trace('translation.route',job_id=job.job_id,message_id=message_id,outcome='automatic_paused')
            self._emit(AppEvent('error',job.generation,{'job_id':job.job_id,'message_id':message_id,'error':'自动翻译已暂停，请先启用。'}));return job.job_id
        self._expire_pending()
        if kind=='manual':
            local=self.local_resolver(text,target)
            if self._replay_capture is not None and self._replay_capture['complete']:
                self._replay_capture['external']['local_results'].append(local)
            if local is not None:
                self._result(job,TranslationResult(job.job_id,local,None,None,0),'已校对本机短句')
                return job.job_id
        cache_key=(self.engine.provider_id,self.engine.config_version,text,source,target)
        key=(self.generation,)+cache_key
        cached=self.cache.get(cache_key)
        if cached is not None:
            self._result(job,cached,'缓存'); return job.job_id
        if self.paused or self.configuration_held:
            self._trace('translation.route',job_id=job.job_id,message_id=message_id,outcome='cloud_paused')
            self._emit(AppEvent('error',job.generation,{'job_id':job.job_id,'message_id':message_id,'error':'云翻译已暂停，请先启用。'})); return job.job_id
        if key in self.subscribers:
            self._trace('translation.route',job_id=job.job_id,message_id=message_id,outcome='coalesced',
                        request_ref=self.subscribers[key][0].job_id)
            if len(self.subscribers[key])>=500:
                self._emit(AppEvent('gap',self.generation,'相同消息订阅已满，请手动重试。'))
            else:
                self.subscribers[key].append(job)
                self._translation_state(job,'translating' if self.active==key else 'queued')
                if kind=='manual' or explicit:self.pending.promote(key)
            return job.job_id
        event=self.messages.get(message_id)
        guarded=kind=='auto' and not explicit and event is not None
        if guarded:
            reason=self.guard.sender_reason(event.channel,event.sender)
            if reason:
                self._suppress(job,reason); return job.job_id
        accepted,displaced=self.pending.put(key,kind=='manual' or explicit)
        if displaced:self._drop(displaced,'等待请求被手动翻译置换，请重试。',reason='queue_full')
        if accepted:
            if guarded:self.guard.record_sender(event.channel,event.sender)
            self.subscribers[key]=[job]
            self._translation_state(job,'queued')
            self.condition.notify()
        else:
            self._trace('translation.route',job_id=job.job_id,message_id=message_id,outcome='queue_full')
            if guarded:self._suppress(job,'queue_full')
            else:self._emit(AppEvent('error',job.generation,{'job_id':job.job_id,'message_id':message_id,'error':'请求队列已满，请重试。'}))
        return job.job_id

    def _suppress(self,job,reason):
        self._trace('translation.discarded',job_id=job.job_id,message_id=job.message_id,outcome=reason)
        self._emit(AppEvent('suppressed',job.generation,{'message_id':job.message_id,'job_id':job.job_id,'reason':reason}))

    def _drop(self,key,message,reason=None):
        for job in self.subscribers.pop(key,[]):
            self._trace('translation.discarded',job_id=job.job_id,message_id=job.message_id,outcome=reason or 'cancelled')
            if reason:self._suppress(job,reason)
            else:self._emit(AppEvent('error',job.generation,{'message_id':job.message_id,'job_id':job.job_id,'error':message}))

    def _expire_pending(self):
        for key in self.pending.expire():self._drop(key,'',reason='expired')

    @record_operation('dispatch')
    def _dispatch_pending(self):
        self._expire_pending()
        if not self.pending.items:
            self.condition.notify_all();return None
        automatic=not self.pending.items[0][1]
        key=self.pending.pop()
        jobs=self.subscribers.get(key,[])
        if not jobs:return None
        job=jobs[0]
        if automatic:
            reason=self.guard.character_reason(job.text)
            if reason:
                self._drop(key,'',reason=reason)
                self.condition.notify_all();return None
            self.guard.record_characters(job.text)
        self.active=key
        for subscriber in jobs:self._translation_state(subscriber,'translating')
        return key,job

    @record_operation('cloud_authorize')
    def _cloud_authorized(self):
        return not (self.stopped or self.paused or self.configuration_held)

    @record_operation('cloud_complete')
    def _complete_translation(self,key,job,result,error):
        subscribers=self.subscribers.pop(key,[])
        if result and not error and not self.stopped:
            if job.generation==self.generation:self.cache.put(key[1:],result)
            for subscriber in subscribers:
                if subscriber.generation==self.generation:self._result(subscriber,result)
                else:self._trace('translation.discarded',job_id=subscriber.job_id,outcome='stale_generation')
        elif error:
            for subscriber in subscribers:
                if subscriber.generation==self.generation:
                    self._emit(AppEvent('error',subscriber.generation,{'message_id':subscriber.message_id,'job_id':subscriber.job_id,'error':error}))
        self.active=None
        self.condition.notify_all()

    def _work(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda:self.stopped or (
                    not self.paused and not self.configuration_held and bool(self.pending.items)))
                if self.stopped:return
                dispatched=self._dispatch_pending()
                if dispatched is None:continue
                key,job=dispatched
            result=None;error=None
            started=time.perf_counter()
            try:
                # Authorize network start under the same lock as pause/hold.
                with self.condition:
                    cancelled=not self._cloud_authorized()
                if cancelled:
                    if not self.stopped:error='云翻译已暂停，待发请求已取消。'
                else:
                    self._trace('translation.request',job_id=job.job_id,message_id=job.message_id,outcome='start')
                    result=self.engine.translate(job)
            except TranslationError as failure:
                error=str(failure)
                self._trace('translation.failure',job_id=job.job_id,stage='request',error_type=type(failure).__name__,
                            error_code=failure.kind,
                            provider_code=getattr(failure,'provider_code',None),elapsed_ms=(time.perf_counter()-started)*1000)
            except Exception as failure:
                error='翻译失败；请检查本机配置后重试。'
                self._trace('translation.failure',job_id=job.job_id,stage='request',error_type=type(failure).__name__)
            with self.condition:
                self._complete_translation(key,job,result,error)

    def drain_events(self,limit=100):
        for _ in range(limit):
            try: event=self.source_events.get_nowait()
            except queue.Empty: break
            self.handle_source_event(event)
        result=[]
        if self.overflow:
            result.append(AppEvent('gap',self.generation,'事件队列已满，存在遗漏；可停止后重连建立新基线。'))
            self.overflow=False
        for _ in range(max(0,limit-len(result))):
            try: result.append(self.events.get_nowait())
            except queue.Empty: break
        return result

    def close(self):
        self.source.stop()
        with self.condition:
            self.stopped=True; self.pending.clear(); self.subscribers.clear(); self.messages.clear()
            self.guard.clear()
            self.context.clear(self.generation)
            self.condition.notify_all()
        self.thread.join(12)
        if not self.thread.is_alive(): self.engine.close()
