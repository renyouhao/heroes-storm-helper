from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class MessageView:
    event: object
    engine_translation: str=''
    status: str='原文'
    suppressed_reason: str=''

    @property
    def original(self): return self.event.text


class AppViewModel:
    def __init__(self):
        self.generation=0; self.messages=OrderedDict(); self.usage_snapshot=None
        self.status='就绪；开始前请确认本月用量。'
        self.expected_job=None; self.copy_text=''; self.manual_provenance=''

    @property
    def can_translate(self): return bool(self.usage_snapshot and self.usage_snapshot.confirmed)

    def apply(self,event):
        if event.kind=='generation':
            if event.generation<self.generation: return 'stale_generation'
            self.generation=event.generation; self.copy_text=''; self.manual_provenance=''
            self.expected_job=None; self.status=str(event.payload); return 'applied'
        if event.generation!=self.generation: return 'stale_generation'
        p=event.payload
        if event.kind=='chat':
            previous=self.messages.get(p.message_id)
            if previous and previous.event.generation!=event.generation:
                # 保留旧行的位置；原始 ID 留给当前代，历史 key 不用于重试。
                history_key=f'history:{previous.event.generation}:{p.message_id}'
                while history_key in self.messages: history_key='history:'+history_key
                self.messages=OrderedDict((history_key if key==p.message_id else key,value)
                                          for key,value in self.messages.items())
            self.messages[p.message_id]=MessageView(p)
            while len(self.messages)>500: self.messages.popitem(last=False)
            return 'applied'
        elif (event.kind=='result' and p['message_id'] in self.messages
              and self.messages[p['message_id']].event.generation==event.generation):
            m=self.messages[p['message_id']]; m.engine_translation=p['engine_translation']; m.status=p.get('provenance','已翻译')
            m.suppressed_reason=''
            return 'applied'
        elif event.kind=='suppressed':
            message=self.messages.get(p.get('message_id'))
            if message and message.event.generation==event.generation:
                message.suppressed_reason=p['reason']
                message.status='防刷屏：暂缓自动翻译，可手动重试'
                return 'applied'
        elif event.kind=='manual_result' and p['job_id']==self.expected_job:
            self.copy_text=p['translated']; self.manual_provenance=p.get('provenance','')
            return 'applied'
        elif event.kind=='error':
            self.status=p['error']
            message=self.messages.get(p.get('message_id'))
            if message and message.event.generation==event.generation: message.status=p['error']
            return 'applied'
        elif event.kind=='usage':
            self.usage_snapshot=p
            return 'applied'
        elif event.kind in ('status','gap'):
            self.status=str(p)
            return 'applied'
        if event.kind=='manual_result':return 'job_mismatch'
        if event.kind in ('result','suppressed'):return 'missing_message'
        return 'ignored_event'
