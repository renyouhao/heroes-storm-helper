from collections import OrderedDict
import unittest

from hots_chat.models import ChatEvent
from hots_chat.ui.overlay_window import OverlayHistory
from hots_chat.ui.viewmodel import MessageView


def row(key,text='need help',*,sender='队友',channel='团队',generation=1,suppressed=''):
    result=MessageView(ChatEvent(key,generation,channel,sender,'1:00',text))
    result.suppressed_reason=suppressed
    return result


class OverlayFoldingTests(unittest.TestCase):
    def test_duplicates_fold_without_extending_expiry_or_modifying_history(self):
        messages=OrderedDict(a=row('a'))
        history=OverlayHistory()
        history.sync(messages,1,now=10,timeout=15)
        messages['b']=row('b',suppressed='发送过快')
        history.sync(messages,1,now=20,timeout=15)
        self.assertEqual(len(messages),2)
        self.assertEqual(len(history.entries),1,'同原文只占一个浮窗位置')
        entry=next(iter(history.entries.values()))
        self.assertEqual(entry.count,2)
        self.assertEqual(entry.expires_at,25)
        history.sync(messages,1,now=24,timeout=15)
        self.assertEqual(entry.count,2,'重复sync不重复计数')
        history.expire(25)
        history.sync(messages,1,now=26,timeout=15)
        self.assertFalse(history.entries)

    def test_sender_channel_and_generation_are_part_of_fold_identity(self):
        messages=OrderedDict(a=row('a'),b=row('b',sender='另一位'),c=row('c',channel='所有玩家'))
        history=OverlayHistory()
        history.sync(messages,1,now=10,timeout=15)
        self.assertEqual(len(history.entries),3)
        messages['d']=row('d',generation=2)
        history.sync(messages,2,now=11,timeout=15)
        self.assertEqual(list(history.entries),['d'])

    def test_unique_suppressed_rows_never_evict_normal_translation(self):
        messages=OrderedDict(a=row('a',text='normal'))
        history=OverlayHistory()
        history.sync(messages,1,now=10,timeout=15)
        for index in range(10):
            key=f's{index}'
            messages[key]=row(key,text=key,suppressed='发送过快')
        history.sync(messages,1,now=11,timeout=15)
        self.assertEqual(list(history.entries),['a'])

    def test_late_suppression_removes_unique_pending_but_keeps_normal_rows(self):
        messages=OrderedDict(a=row('a',text='normal'),b=row('b',text='spam'))
        history=OverlayHistory()
        history.sync(messages,1,now=10,timeout=15)
        messages['b'].suppressed_reason='发送过快'
        history.sync(messages,1,now=11,timeout=15)
        self.assertEqual(list(history.entries),['a'])

    def test_delayed_suppression_restores_normal_slot_with_original_deadline(self):
        messages=OrderedDict(a=row('a',text='normal'))
        history=OverlayHistory()
        history.sync(messages,1,now=10,timeout=15)
        for key in ('b','c','d'):
            messages[key]=row(key,text=key)
        history.sync(messages,1,now=11,timeout=15)
        for key in ('b','c','d'):
            messages[key].suppressed_reason='发送过快'
        history.sync(messages,1,now=12,timeout=15)
        self.assertEqual(list(history.entries),['a'])
        self.assertEqual(history.next_expiry,25)

    def test_translation_on_any_duplicate_updates_folded_entry_and_state_is_bounded(self):
        messages=OrderedDict((str(i),row(str(i))) for i in range(500))
        history=OverlayHistory()
        history.sync(messages,1,now=10,timeout=15)
        messages['499'].engine_translation='需要支援'
        history.sync(messages,1,now=11,timeout=15)
        entry=next(iter(history.entries.values()))
        self.assertEqual(entry.message.engine_translation,'需要支援')
        self.assertEqual(entry.count,500)
        messages.clear()
        history.sync(messages,1,now=12,timeout=15)
        self.assertFalse(history.entries)
        self.assertFalse(history._seen)

