import ctypes
import gc
import threading
from ctypes import wintypes
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import weakref

from hots_chat.models import AppEvent

try:
    from hots_chat.ui.quick_input import QuickInput
    from hots_chat.ui.input_native import WindowsInputNative
except ImportError:
    QuickInput = WindowsInputNative = None


class Value:
    def __init__(self, value=''):
        self.value = value
        self.callbacks = []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        for callback in self.callbacks:
            callback()

    def trace_add(self, mode, callback):
        self.callbacks.append(callback)


class TimerRoot:
    def __init__(self):
        self.callbacks = {}
        self.next_id = 0

    def after(self, delay, callback):
        self.next_id += 1
        self.callbacks[self.next_id] = callback
        return self.next_id

    def after_cancel(self, timer):
        self.callbacks.pop(timer, None)

    def expire(self):
        callbacks, self.callbacks = self.callbacks, {}
        for callback in callbacks.values():
            callback()


class Window:
    def __init__(self, handle):
        self.handle = handle
        self.visible = False
        self.destroyed = False
        self.focused = 0
        self.selections = []
        self.cursor = None

    def winfo_id(self):
        return self.handle

    def deiconify(self):
        self.visible = True

    def withdraw(self):
        self.visible = False

    def lift(self):
        pass

    def focus_force(self):
        self.focused += 1

    def focus_set(self):
        self.focused += 1

    def selection_range(self, first, last):
        self.selections.append((first, last))

    def icursor(self, index):
        self.cursor = index

    def destroy(self):
        self.destroyed = True


class Native:
    def __init__(self):
        self.foreground = 99
        self.composing = False
        self.return_guard = False
        self.restored = []
        self.attached = []
        self.detached = False

    def foreground_window(self):
        return self.foreground

    def window_handle(self, handle):
        return handle

    def activate(self, handle):
        self.foreground = handle

    def restore_foreground(self, previous, own):
        if self.foreground == own and previous:
            self.restored.append(previous)
            self.foreground = previous
            return True
        return False

    def attach_ime(self, handle):
        self.attached.append(handle)

    def return_is_composing(self, handle):
        return self.composing or self.return_guard

    def clear_return_guard(self):
        self.return_guard = False

    def detach_ime(self):
        self.detached = True


class Controller:
    def __init__(self):
        self.generation = 7
        self.submitted = []
        self.failure = None

    def submit_manual(self, text, target):
        if self.failure:
            raise self.failure
        job = 'job-' + str(len(self.submitted) + 1)
        self.submitted.append((text, target, job))
        return job


class QuickInputTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(QuickInput, '快捷输入模块尚未实现')
        self.root = TimerRoot()
        self.controller = Controller()
        self.settings = NS(target_language='ko', font_size=12)
        self.native = Native()
        self.clipboard = []
        self.saved_targets = []
        self.window = QuickInput(
            self.root, self.controller, self.settings,
            lambda: self.saved_targets.append(self.settings.target_language),
            native=self.native, clipboard=self.clipboard.append,
        )
        self.created = 0

        def build():
            self.created += 1
            self.window.window = Window(100)
            self.window.entry = Window(101)
            self.window.text = Value()
            self.window.target = Value(self.settings.target_language)
            self.window.status = Value()
            self.window.text.trace_add('write', self.window._on_edit)

        self.window._create_window = build
        self.window.refresh_layout = lambda *args, **kwargs: False

    def submit(self, text='撤退'):
        self.window.open()
        self.window.text.set(text)
        self.window._on_return_press()
        self.window._on_return_release()
        return self.controller.submitted[-1][2]

    def result(self, job, translated='Retreat!', generation=7):
        return AppEvent('manual_result', generation, {
            'job_id': job, 'translated': translated, 'provenance': '已校对本机短句',
            'message_id': None, 'engine_translation': translated,
            'detected_language': None, 'original': '撤退', 'target': 'ko',
        })

    def test_window_is_lazy_and_reuses_its_own_toplevel(self):
        self.assertEqual(self.created, 0)
        self.window.open()
        self.window.open()
        self.assertEqual(self.created, 1)
        self.assertEqual(self.native.attached, [101])

    def test_enter_translates_then_copies_only_translation_once(self):
        job = self.submit()
        self.assertEqual(self.controller.submitted, [('撤退', 'ko', 'job-1')])
        self.assertEqual(self.clipboard, [])
        self.window.handle_event(self.result(job))
        self.window.handle_event(self.result(job))
        self.assertEqual(self.clipboard, ['Retreat!'])
        self.assertIn('已复制', self.window.status.get())
        self.assertEqual(self.native.restored, [99])
        self.root.expire()
        self.assertFalse(self.window.window.visible)

    def test_other_jobs_cannot_overwrite_current_translation(self):
        job = self.submit()
        self.window.handle_event(self.result('main-window-job', 'wrong'))
        self.assertEqual(self.clipboard, [])
        self.window.handle_event(self.result(job, 'correct'))
        self.assertEqual(self.clipboard, ['correct'])

    def test_editing_draft_rejects_late_result_even_after_reverting(self):
        job = self.submit()
        self.window.text.set('集合')
        self.window.text.set('撤退')
        self.window.handle_event(self.result(job))
        self.assertEqual(self.clipboard, [])

    def test_language_switch_invalidates_job_and_is_persisted(self):
        job = self.submit()
        self.assertEqual(self.window._toggle_target(), 'break')
        self.assertEqual(self.settings.target_language, 'en')
        self.assertEqual(self.saved_targets, ['en'])
        self.window.handle_event(self.result(job))
        self.assertEqual(self.clipboard, [])
        self.window._on_return_press()
        self.window._on_return_release()
        self.assertEqual(self.controller.submitted[-1][:2], ('撤退', 'en'))

    def test_cancel_preserves_draft_but_old_job_never_copies_after_reopen(self):
        job = self.submit()
        self.window.cancel()
        self.assertFalse(self.window.window.visible)
        self.window.open()
        self.assertEqual(self.window.text.get(), '撤退')
        self.window.handle_event(self.result(job))
        self.assertEqual(self.clipboard, [])

    def test_generation_change_without_generation_event_rejects_old_result(self):
        job = self.submit()
        self.controller.generation = 8
        self.window.handle_event(self.result(job))
        self.assertEqual(self.clipboard, [])
        self.assertEqual(self.window.text.get(), '撤退')

    def test_generation_event_invalidates_job(self):
        job = self.submit()
        self.window.handle_event(AppEvent('generation', 8, '已重连'))
        self.window.handle_event(self.result(job))
        self.assertEqual(self.clipboard, [])

    def test_error_preserves_input_and_retry_uses_a_new_job(self):
        job = self.submit()
        self.window.handle_event(AppEvent('error', 7, {'job_id': job, 'message_id': None, 'error': '额度未确认'}))
        self.assertEqual(self.window.text.get(), '撤退')
        self.assertIn('额度未确认', self.window.status.get())
        self.window._on_return_press()
        self.window._on_return_release()
        self.window.handle_event(self.result(job, 'stale'))
        self.window.handle_event(self.result('job-2', 'retry'))
        self.assertEqual(self.clipboard, ['retry'])

    def test_submit_validation_failure_keeps_input(self):
        self.window.open()
        self.window.text.set('撤退')
        self.controller.failure = ValueError('不允许提交')
        self.window._on_return_press()
        self.window._on_return_release()
        self.assertEqual(self.window.text.get(), '撤退')
        self.assertIn('不允许提交', self.window.status.get())
        self.assertEqual(self.clipboard, [])

    def test_clipboard_failure_keeps_draft_and_does_not_restore_focus(self):
        job = self.submit()

        def failed_copy(text):
            raise RuntimeError('clipboard busy')

        self.window._clipboard = failed_copy
        self.window.handle_event(self.result(job))
        self.assertEqual(self.window.text.get(), '撤退')
        self.assertIn('复制失败', self.window.status.get())
        self.assertEqual(self.native.restored, [])
        self.assertTrue(self.window.window.visible)

    def test_quick_reopen_and_second_submit_reject_first_result(self):
        first = self.submit()
        second = self.submit('集合')
        self.window.handle_event(self.result(first, 'stale'))
        self.window.handle_event(self.result(second, 'Gather!'))
        self.assertEqual(self.clipboard, ['Gather!'])
        self.assertEqual(self.native.restored, [99])

    def test_user_switching_to_another_app_does_not_copy_late_translation(self):
        job = self.submit()
        self.native.foreground = 555
        self.window._on_focus_out()
        self.window.handle_event(self.result(job))
        self.assertEqual(self.clipboard, [])
        self.assertEqual(self.native.restored, [])
        self.assertEqual(self.native.foreground, 555)
        self.native.foreground = 100
        self.window.handle_event(self.result(job))
        self.assertEqual(self.clipboard, [],'回到输入条也不能恢复已取消的自动复制')

    def test_internal_focus_change_keeps_pending_translation(self):
        job=self.submit()
        self.window._on_focus_out()
        self.window.handle_event(self.result(job))
        self.assertEqual(self.clipboard,['Retreat!'])

    def test_failed_native_initialization_can_retry_with_ime_protection(self):
        original=self.native.attach_ime
        with patch.object(self.native,'attach_ime',side_effect=OSError('IME init failed')):
            with self.assertRaises(OSError):self.window.open()
        self.assertIsNone(self.window.window)
        self.window.open()
        self.assertEqual(self.native.attached,[101])
        self.assertEqual(self.created,2)

    def test_reopening_cancels_previous_success_hint_timer(self):
        job = self.submit()
        self.window.handle_event(self.result(job))
        self.window.open()
        self.root.expire()
        self.assertTrue(self.window.window.visible)

    def test_ime_confirm_enter_waits_for_next_distinct_enter(self):
        self.window.open()
        self.window.text.set('撤')
        self.native.composing = True
        self.window._on_return_press()
        self.native.composing = False
        self.window.text.set('撤退')
        self.window._on_return_release()
        self.assertEqual(self.controller.submitted, [])
        self.window._on_return_press()
        self.window._on_return_release()
        self.assertEqual(self.controller.submitted[-1][:2], ('撤退', 'ko'))

    def test_ime_consumed_keypress_does_not_submit_on_key_release(self):
        self.window.open()
        self.window.text.set('撤退')
        self.window._on_return_release()
        self.assertEqual(self.controller.submitted, [])

    def test_ime_end_before_tk_keypress_still_blocks_confirmation_enter(self):
        self.window.open()
        self.window.text.set('撤退')
        self.native.return_guard = True
        self.window._on_return_press()
        self.window._on_return_release()
        self.assertEqual(self.controller.submitted, [])
        self.window._on_return_press()
        self.window._on_return_release()
        self.assertEqual(len(self.controller.submitted), 1)

    def test_held_enter_submits_only_after_release_once(self):
        self.window.open()
        self.window.text.set('撤退')
        for _ in range(4):
            self.window._on_return_press()
        self.assertEqual(self.controller.submitted, [])
        self.window._on_return_release()
        self.window._on_return_release()
        self.assertEqual(len(self.controller.submitted), 1)

    def test_losing_focus_between_press_and_release_cannot_submit(self):
        self.window.open()
        self.window.text.set('撤退')
        self.window._on_return_press()
        self.window._on_focus_out()
        self.window._on_return_release()
        self.assertEqual(self.controller.submitted, [])

    def test_close_invalidates_jobs_cancels_timer_and_detaches_ime(self):
        job = self.submit()
        self.window.handle_event(self.result(job))
        self.window.close()
        self.window.close()
        self.assertEqual(self.root.callbacks, {})
        self.assertTrue(self.native.detached)
        self.assertTrue(self.window.window.destroyed)
        self.window.handle_event(self.result(job, 'late'))
        self.window.open()
        self.assertEqual(self.clipboard, ['Retreat!'])
        self.assertEqual(self.created, 1)

    def test_close_releases_owner_on_ui_thread_after_final_visibility_callback(self):
        gc.collect()
        enabled = gc.isenabled()
        gc.disable()
        changes, finalized = [], []

        class Owner:
            def target_changed(self):pass
            def position(self):return None
            def visible(self,value):
                changes.append((value,self.window.on_target_changed is not None))

        try:
            owner = Owner()
            owner.window = self.window
            self.window.on_target_changed = owner.target_changed
            self.window.on_visibility_changed = owner.visible
            self.window._position_provider = owner.position
            watched = weakref.ref(owner)
            weakref.finalize(owner,lambda:finalized.append(threading.get_ident()))
            self.window.open();self.window.cancel()
            self.assertIs(self.window.on_target_changed.__self__,owner)
            self.window.open();self.window.close()
            self.assertEqual(changes,[(True,True),(False,True),(True,True),(False,True)])
            del owner
            worker = threading.Thread(target=gc.collect,name='inert-gc-worker')
            worker.start();worker.join(2)
            self.assertIsNone(watched())
            self.assertEqual(finalized,[threading.main_thread().ident])
            self.assertIsNone(self.window.on_target_changed)
            self.assertIsNone(self.window.on_visibility_changed)
            self.assertIsNone(self.window._position_provider)
        finally:
            if enabled:gc.enable()


class Function:
    def __init__(self, result=0):
        self.result = result
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if callable(self.result):
            return self.result(*args)
        return self.result


def fake_libraries():
    user = NS(**{name: Function() for name in (
        'GetForegroundWindow', 'GetAncestor', 'IsWindow', 'SetForegroundWindow', 'GetAsyncKeyState',
    )})
    user.SetForegroundWindow.result = 1
    imm = NS(ImmGetContext=Function(321), ImmGetCompositionStringW=Function(), ImmReleaseContext=Function(1))
    controls = NS(SetWindowSubclass=Function(1), RemoveWindowSubclass=Function(1), DefSubclassProc=Function(17))
    return user, imm, controls


class InputNativeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(WindowsInputNative, '输入条原生模块尚未实现')
        self.user, self.imm, self.controls = fake_libraries()
        self.native = WindowsInputNative(user=self.user, imm=self.imm, controls=self.controls)

    def test_win32_handles_and_callbacks_keep_pointer_width(self):
        self.assertEqual(self.user.GetForegroundWindow.restype, wintypes.HWND)
        self.assertEqual(self.user.SetForegroundWindow.argtypes, [wintypes.HWND])
        self.assertEqual(self.user.GetAncestor.restype, wintypes.HWND)
        self.assertEqual(self.imm.ImmGetContext.restype, wintypes.HANDLE)
        high_handle = 0x12345678ABCD
        self.user.GetForegroundWindow.result = high_handle
        self.assertEqual(self.native.foreground_window(), high_handle)

    def test_composition_byte_count_always_releases_input_context(self):
        self.imm.ImmGetCompositionStringW.result = 4
        self.assertTrue(self.native.return_is_composing(101))
        self.assertEqual(self.imm.ImmGetCompositionStringW.calls, [(321, 8, None, 0)])
        self.assertEqual(self.imm.ImmReleaseContext.calls, [(101, 321)])
        self.imm.ImmGetCompositionStringW.result = 0
        self.assertFalse(self.native.return_is_composing(101))

    def test_no_context_is_not_an_active_composition(self):
        self.imm.ImmGetContext.result = 0
        self.assertFalse(self.native.return_is_composing(101))
        self.assertEqual(self.imm.ImmReleaseContext.calls, [])

    def test_restore_requires_own_foreground_and_live_previous_window(self):
        self.user.GetForegroundWindow.result = 100
        self.user.IsWindow.result = 1
        self.assertTrue(self.native.restore_foreground(99, 100))
        self.assertEqual(self.user.SetForegroundWindow.calls, [(99,)])
        self.user.SetForegroundWindow.calls.clear()
        self.user.GetForegroundWindow.result = 555
        self.assertFalse(self.native.restore_foreground(99, 100))
        self.assertEqual(self.user.SetForegroundWindow.calls, [])
        self.user.GetForegroundWindow.result = 100
        self.user.IsWindow.result = 0
        self.assertFalse(self.native.restore_foreground(99, 100))
        self.assertEqual(self.user.SetForegroundWindow.calls, [])

    def test_native_ime_end_guard_survives_until_confirmation_release(self):
        self.native.attach_ime(101)
        callback = self.controls.SetWindowSubclass.calls[-1][1]
        self.user.GetAsyncKeyState.result = -32768
        callback(101, 0x010D, 0, 0, 1, 0)  # WM_IME_STARTCOMPOSITION
        callback(101, 0x010E, 0, 0, 1, 0)  # WM_IME_ENDCOMPOSITION
        self.assertTrue(self.native.return_is_composing(101))
        self.native.clear_return_guard()
        self.assertFalse(self.native.return_is_composing(101))
        self.native.detach_ime()
        self.assertEqual(len(self.controls.RemoveWindowSubclass.calls), 1)

    def test_mouse_candidate_selection_does_not_swallow_next_enter(self):
        self.native.attach_ime(101)
        callback = self.controls.SetWindowSubclass.calls[-1][1]
        callback(101, 0x010D, 0, 0, 1, 0)
        self.user.GetAsyncKeyState.result = 0
        callback(101, 0x010E, 0, 0, 1, 0)
        self.assertFalse(self.native.return_is_composing(101))


if __name__ == '__main__':
    unittest.main()
