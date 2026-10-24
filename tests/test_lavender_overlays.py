"""B becomes the real lavender theme; layout and input state stay unchanged."""
from collections import OrderedDict
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import unittest

from hots_chat.layout import DisplayMetrics
from hots_chat.models import ChatEvent
from hots_chat.ui import theme
from hots_chat.ui.helper_notice import _NoticeOverlay
from hots_chat.ui.overlay_window import OverlayWindow
from hots_chat.ui.quick_input import QuickInput
from hots_chat.ui.viewmodel import MessageView
from hots_chat.window_settings import WindowSettings


class OfflineNative:
    def set_overlay_mode(self, window, editing):
        pass

    def show_no_activate(self, window):
        pass

    def return_is_composing(self, handle):
        return False

    def detach_ime(self):
        pass


class LavenderOverlayTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.tk.call('tk', 'scaling', 96 / 72)
        self.settings = WindowSettings(color_theme='lavender', hotkeys={}, overlay_original=True,
                                       overlay_style='panel', overlay_placement='chat',
                                       overlay_x=10400, overlay_y=10400)
        self.metrics = DisplayMetrics((10000, 10000, 11920, 11080), 1.)
        self.style = theme.apply_theme(self.root, self.settings)
        self.cleanups = []

    def tearDown(self):
        for cleanup in reversed(self.cleanups):
            cleanup()
        self.root.update_idletasks()
        self.root.destroy()

    def quick(self):
        controller = SimpleNamespace(generation=1, submit_manual=lambda text, target: 'manual-job')
        quick = QuickInput(self.root, controller, self.settings, metrics=self.metrics,
                           native=OfflineNative(), clipboard=lambda text: None)
        self.cleanups.append(quick.close)
        quick._create_window()
        self.root.update_idletasks()
        return quick

    def overlay(self):
        overlay = OverlayWindow(self.root, self.settings, metrics=self.metrics,
                                native=OfflineNative(), clock=lambda: 10.)
        self.cleanups.append(overlay.close)
        messages = OrderedDict()
        for key in ('a', 'b'):
            event = ChatEvent(key, 1, '团队', '队友', '14:21', 'Group up ' + key)
            messages[key] = MessageView(event, '先集合 ' + key)
        overlay.sync(messages, 1)
        return overlay

    def test_lavender_input_and_popup_share_main_palette_with_purple_action(self):
        quick = self.quick()
        colors = theme.theme_colors(self.settings)
        self.assertEqual(quick.window.cget('background'), colors['background'])
        self.assertEqual(self.style.lookup('Overlay.TFrame', 'background'), colors['background'])
        self.assertEqual(self.style.lookup('Overlay.TEntry', 'fieldbackground'), colors['field'])
        self.assertEqual(self.style.lookup('Overlay.TEntry', 'foreground'), colors['text'])
        self.assertEqual(self.style.lookup('Overlay.Accent.TButton', 'background'), colors['accent'])
        self.assertEqual(self.style.lookup('Overlay.Accent.TButton', 'foreground'), colors['on_accent'])
        popup = self.root.tk.call('ttk::combobox::PopdownWindow', str(quick.target_control))
        listbox = popup + '.f.l'
        for name in ('lavender', 'graphite', 'mint', 'moonlight', 'lavender'):
            self.settings.color_theme = name
            theme.apply_theme(self.root, self.settings)
            quick.refresh_layout(force=True)
            palette = theme.overlay_colors(self.settings)
            self.assertEqual(self.root.tk.call(listbox, 'cget', '-background'), palette['field'])
            self.assertEqual(self.root.tk.call(listbox, 'cget', '-foreground'), palette['text'])
            self.assertEqual(self.root.tk.call(listbox, 'cget', '-selectbackground'), palette['selection'])

    def test_lavender_panel_and_helper_notice_use_semantic_colors(self):
        colors = theme.theme_colors(self.settings)
        overlay = self.overlay()
        self.assertEqual(overlay.window.cget('background'), colors['surface'])
        self.assertEqual(overlay.rows[0]['translation'].cget('foreground'), colors['text'])
        self.assertEqual(overlay.rows[0]['original'].cget('foreground'), colors['muted'])
        self.assertEqual(overlay.window.cget('highlightbackground'), colors['accent'])
        self.assertEqual(len(overlay.rows), 2)
        notice = _NoticeOverlay(self.root, OfflineNative())
        self.cleanups.append(notice.close)
        notice.render('请手动选择', '尚未确认这层天赋。', self.settings, self.metrics)
        self.assertEqual(notice.canvas.itemcget('surface', 'fill'), colors['surface'])
        self.assertEqual(notice.canvas.itemcget('detail', 'fill'), colors['text'])
        self.assertEqual(notice.canvas.itemcget('title', 'fill'), colors['warning'])

    def test_theme_switch_keeps_input_widgets_draft_pending_job_and_layout(self):
        quick = self.quick()
        quick._visible = True
        quick.text.set('我们等大招好了再开团')
        quick._submit()
        widgets = (quick.window, quick.entry, quick.target_control, quick.submit_button, quick.status_label)
        pending, revision = quick._pending, quick._revision
        text, status = quick.text.get(), quick.status.get()
        quick._hide_timer = self.root.after(60000, lambda: None)
        timer = quick._hide_timer
        input_row = quick.entry.master
        order = tuple(child.winfo_class() for child in input_row.winfo_children())
        packing = tuple(dict(child.pack_info()) for child in input_row.winfo_children())
        geometry = quick.window.geometry()
        entry_padding = self.style.lookup('Overlay.TEntry', 'padding')
        button_padding = self.style.lookup('Overlay.Accent.TButton', 'padding')
        layouts = {name: self.style.layout(name) for name in ('Overlay.TEntry', 'Overlay.TCombobox', 'Overlay.Accent.TButton')}
        for name in ('graphite', 'lavender', 'moonlight', 'lavender', 'mint', 'lavender'):
            self.settings.color_theme = name
            theme.apply_theme(self.root, self.settings)
            quick.refresh_layout(force=True)
            self.root.update_idletasks()
            self.assertEqual(widgets, (quick.window, quick.entry, quick.target_control, quick.submit_button, quick.status_label))
            self.assertIs(quick._pending, pending)
            self.assertEqual((quick._revision, quick.text.get(), quick.status.get(), quick._hide_timer),
                             (revision, text, status, timer))
            self.assertTrue(quick.submit_button.instate(['disabled']))
            self.assertEqual(tuple(child.winfo_class() for child in input_row.winfo_children()), order)
            self.assertEqual(tuple(dict(child.pack_info()) for child in input_row.winfo_children()), packing)
            self.assertEqual(quick.window.geometry(), geometry)
            self.assertEqual(self.style.lookup('Overlay.TEntry', 'padding'), entry_padding)
            self.assertEqual(self.style.lookup('Overlay.Accent.TButton', 'padding'), button_padding)
            self.assertEqual({key: self.style.layout(key) for key in layouts}, layouts)

    def test_theme_switch_preserves_panel_history_timer_geometry_and_subtitle_white_outline(self):
        overlay = self.overlay()
        window = overlay.window
        history = [(key, id(entry), entry.expires_at) for key, entry in overlay.history.entries.items()]
        timer = overlay._timer
        geometry = window.geometry()
        fonts = (overlay._translation_font.actual(), overlay._original_font.actual())
        for name in ('graphite', 'lavender', 'moonlight', 'mint', 'lavender'):
            self.settings.color_theme = name
            theme.apply_theme(self.root, self.settings)
            overlay.apply_settings()
            self.root.update_idletasks()
            self.assertIs(overlay.window, window)
            self.assertEqual([(key, id(entry), entry.expires_at) for key, entry in overlay.history.entries.items()], history)
            self.assertEqual(overlay._timer, timer)
            self.assertEqual(window.geometry(), geometry)
            self.assertEqual((overlay._translation_font.actual(), overlay._original_font.actual()), fonts)
        self.settings.overlay_style = 'subtitle'
        for name in ('lavender', 'graphite', 'moonlight', 'mint'):
            self.settings.color_theme = name
            theme.apply_theme(self.root, self.settings)
            overlay.apply_settings()
            canvas = overlay.subtitle_canvas
            self.assertEqual(canvas.itemcget('foreground', 'fill'), '#ffffff')
            self.assertEqual(canvas.itemcget('outline', 'fill'), '#141414')
            self.assertEqual(str(window.attributes('-transparentcolor')).lower(), '#010203')
            self.assertEqual(float(window.attributes('-alpha')), 1.)


if __name__ == '__main__':
    unittest.main()
