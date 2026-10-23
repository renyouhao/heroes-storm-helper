import tkinter as tk
import gc
import threading
from types import SimpleNamespace
import unittest
import weakref

from hots_chat.update_service import UpdateStatus
from hots_chat.window_settings import WindowSettings


class UpdateDialogTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk(); self.root.withdraw()
        self.root.geometry('500x400+50000+50000')
        self.addCleanup(self.root.destroy)

    def dialog(self):
        from hots_chat.ui.update_dialog import UpdateDialog
        calls = []
        service = SimpleNamespace(latest=UpdateStatus(), busy=False,
                                  check=lambda:calls.append('check') or True,
                                  download=lambda:calls.append('download') or True,
                                  cancel=lambda:calls.append('cancel'))
        class Owner:
            pass

        window = Owner()
        window.__dict__.update(root=self.root,settings=WindowSettings(),
                               install_update=lambda:calls.append('install'),update_service=service,
                               updates_can_install=True)
        dialog = UpdateDialog(window, show=False)
        window.update_dialog = dialog
        self.addCleanup(dialog.close)
        return dialog, service, calls

    def test_open_is_offline_until_user_clicks_check(self):
        dialog, _, calls = self.dialog()
        self.assertEqual(calls, [])
        dialog.check_button.invoke()
        self.assertEqual(calls, ['check'])

    def test_download_and_install_buttons_reflect_verified_status(self):
        dialog, service, calls = self.dialog()
        self.assertTrue(dialog.install_button.instate(['disabled']))
        service.latest = UpdateStatus('available', release=SimpleNamespace(version='1.2',notes='改动'))
        dialog.refresh()
        self.assertFalse(dialog.download_button.instate(['disabled']))
        self.assertTrue(dialog.install_button.instate(['disabled']))
        service.latest = UpdateStatus('downloaded', staged=object())
        dialog.refresh()
        dialog.install_button.invoke()
        self.assertEqual(calls, ['install'])

    def test_close_cancels_network_but_does_not_cancel_launched_installer(self):
        dialog, service, calls = self.dialog()
        service.busy = True
        service.latest = UpdateStatus('preparing')
        dialog.close()
        self.assertEqual(calls, [])

    def test_notes_are_plain_text_and_retained_on_error(self):
        dialog, service, _ = self.dialog()
        service.latest = UpdateStatus('available',release=SimpleNamespace(version='1.2',notes='<script>alert(1)</script>'))
        dialog.refresh()
        self.assertIn('<script>', dialog.notes.get('1.0','end'))
        self.assertIn('1.2', dialog.version.get())

    def test_source_mode_does_not_offer_install_button(self):
        dialog, service, _ = self.dialog()
        dialog.owner.updates_can_install = False
        service.latest = UpdateStatus('downloaded', staged=object())
        dialog.refresh()
        self.assertTrue(dialog.install_button.instate(['disabled']))
        self.assertIn('源码',dialog.detail.get())

    def test_source_download_exposes_selectable_archive_path_and_expiration(self):
        dialog, service, _ = self.dialog()
        dialog.owner.updates_can_install = False
        service.latest = UpdateStatus('downloaded', staged=SimpleNamespace(archive_path='D:/temporary/package.zip'))
        dialog.refresh()
        self.assertEqual(dialog.archive_path.get(), 'D:/temporary/package.zip')
        self.assertEqual(str(dialog.archive_entry.cget('state')), 'readonly')
        dialog.archive_entry.selection_range(0, 'end')
        self.assertTrue(dialog.archive_entry.selection_present())
        self.assertIn('关闭', dialog.archive_hint.cget('text'))
        service.latest = UpdateStatus('checking')
        dialog.refresh()
        self.assertEqual(dialog.archive_path.get(), '')

    def test_close_releases_owner_and_service_before_worker_garbage_collection(self):
        gc.collect()
        enabled = gc.isenabled()
        gc.disable()
        finalized = []
        try:
            dialog,_,_ = self.dialog()
            owner = dialog.owner
            watched = weakref.ref(owner)
            weakref.finalize(owner,lambda:finalized.append(threading.get_ident()))
            dialog.close()
            del owner
            worker = threading.Thread(target=gc.collect,name='inert-gc-worker')
            worker.start();worker.join(2)
            self.assertIsNone(watched())
            self.assertIsNone(dialog.owner)
            self.assertIsNone(dialog.service)
            self.assertIsNone(dialog._timer)
            self.assertEqual(finalized,[threading.main_thread().ident])
            # A callback already dispatched before close is now harmless.
            dialog._pump()
        finally:
            if enabled:gc.enable()
