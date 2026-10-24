"""B preview: production OverlayWindow/QuickInput with palette-only changes.

No game, account, preferences file, global hook or key sender is constructed.
The selected B palette now comes directly from the production theme.
"""
from collections import OrderedDict
import tkinter as tk
from tkinter import ttk

from hots_chat.layout import DisplayMetrics
from hots_chat.models import AppEvent, ChatEvent
from hots_chat.ui import overlay_window, quick_input, theme
from hots_chat.ui.viewmodel import MessageView
from hots_chat.window_settings import WindowSettings


def _widget_layout(widget):
    """Serializable structural evidence, without palette or object-path noise."""
    result = []

    def visit(current, parent):
        index = len(result)
        manager = current.winfo_manager()
        placement = {}
        if manager == 'pack':
            placement = {key: str(value) for key, value in current.pack_info().items() if key != 'in'}
        row = {'index': index, 'parent': parent, 'class': current.winfo_class(),
               'manager': manager, 'placement': placement,
               'requested_size': [current.winfo_reqwidth(), current.winfo_reqheight()]}
        for name in ('text', 'width', 'height', 'padding', 'padx', 'pady', 'style', 'values'):
            if name in current.keys():
                row[name] = str(current.cget(name))
        result.append(row)
        for child in current.winfo_children():
            visit(child, index)

    visit(widget, None)
    return result


class _OfflineController:
    generation = 0

    def __init__(self, owner, translate):
        self.owner = owner
        self.translate = translate
        self.sequence = 0
        self.timers = set()

    def submit_manual(self, text, target):
        if not text.strip() or target not in ('en', 'ko'):
            raise ValueError('请输入中文，并选择英语或韩语。')
        if len(text) > 1000:
            raise ValueError('离线预览请输入不超过 1000 个字符。')
        self.sequence += 1
        job = 'recolor-preview-' + str(self.sequence)
        timer = None

        def deliver():
            self.timers.discard(timer)
            if self.owner._closed:
                return
            try:
                translated = self.translate(text, target)
                if not isinstance(translated, str) or not translated.strip():
                    raise ValueError('没有可用的离线译文。')
                event = AppEvent('manual_result', 0, {'job_id': job, 'translated': translated})
            except ValueError:
                detail = '离线词典暂未收录这句话，请选择演示短句或输入“你好”；预览不会调用云翻译。'
                event = AppEvent('error', 0, {'job_id': job, 'error': detail})
                self.owner.notify(detail)
            except Exception:
                detail = '离线演示翻译暂时失败，请重新选择演示短句。'
                event = AppEvent('error', 0, {'job_id': job, 'error': detail})
                self.owner.notify(detail)
            self.owner.quick.handle_event(event)

        timer = self.owner.root.after(250, deliver)
        self.timers.add(timer)
        return job

    def cancel_pending(self):
        for timer in self.timers:
            try:
                self.owner.root.after_cancel(timer)
            except tk.TclError:
                pass
        self.timers.clear()


class RecolorPreview:
    def __init__(self, root, *, metrics: DisplayMetrics, messages: list[dict], notify, translate):
        self.root = root
        self.metrics = metrics
        self.notify = notify
        self._closed = False
        self._mode = None
        self._area = None
        self._light_background = False
        self._messages = []
        self.settings = WindowSettings(color_theme='lavender', hotkeys={}, target_language='en')
        self.settings.overlay_enabled = True
        # These are the production defaults: 420 logical px, 11 pt, original
        # text hidden, native panel opacity and native 10 px rounding.
        self.palette = theme.overlay_colors(self.settings)
        self.style = ttk.Style(root)
        if not hasattr(root._root(), '_hots_rounded_assets'):
            theme.apply_theme(root, self.settings)
        self.controller = _OfflineController(self, translate)
        self.overlay = overlay_window.OverlayWindow(root, self.settings, metrics=metrics, clock=lambda: 100.)
        self.overlay.set_input_active(True)
        self.quick = quick_input.QuickInput(root, self.controller, self.settings,
                                           metrics=metrics, on_target_changed=self._target_changed,
                                           on_visibility_changed=self._input_visibility)
        self._draft = '我们等大招好了再开团'
        self.set_messages(messages)

    def _target_changed(self):
        self.notify('仅切换离线演示目标语言；没有保存或修改正式设置。')

    def _input_visibility(self, visible):
        if not self._closed:
            self.overlay.set_input_active(bool(visible or self._mode not in ('panel', 'subtitle')))

    def _message_views(self):
        result = OrderedDict()
        for index, row in enumerate(self._messages):
            key = 'recolor-' + str(index)
            event = ChatEvent(key, 0, str(row.get('channel', '团队')), str(row.get('sender', '队友')),
                              str(row.get('time', '14:22')), str(row.get('original', '')))
            result[key] = MessageView(event, str(row.get('translated', '')), '离线预览')
        return result

    def show(self, mode: str, area: tuple):
        if self._closed:
            return
        if mode not in ('subtitle', 'panel', 'input'):
            raise ValueError('预览模式只能选择 subtitle、panel 或 input。')
        if len(area) != 4 or area[2] <= 0 or area[3] <= 0:
            raise ValueError('预览舞台必须有正的宽度和高度。')
        was_input = self._mode == 'input'
        self._mode = mode
        self._area = tuple(round(value) for value in area)
        x, y, width, height = self._area
        stage = DisplayMetrics((x, y, x + width, y + height), self.metrics.scale)
        self.overlay.set_input_active(True)
        if mode == 'input':
            self.quick.refresh_layout(stage, force=True)
            if not was_input:
                self.quick.open()
                if not self.quick.text.get():
                    self.quick.text.set(self._draft)
                    self.quick.entry.icursor(tk.END)
                self.quick.status.set(quick_input.IDLE_HINT)
        else:
            self.controller.cancel_pending()
            self.quick.cancel()
            self.settings.overlay_style = mode
            self.settings.overlay_placement = 'bottom'
            self.overlay.apply_settings(stage)
            self.overlay.sync(self._message_views(), 0)
            self.overlay.set_input_active(False)

    def hide(self):
        if self._closed:
            return
        self._mode = None
        self.controller.cancel_pending()
        self.quick.cancel()
        self.overlay.set_input_active(True)

    def set_messages(self, messages):
        if self._closed:
            return
        self._messages = [dict(message) for message in messages]
        # OverlayHistory.clear intentionally keeps deduplication in the real
        # app. Replacing fixture content explicitly starts a fresh preview,
        # including replacements made while the input window is open.
        self.overlay.history.generation = None
        if self._mode in ('panel', 'subtitle'):
            self.overlay.sync(self._message_views(), 0)

    def set_draft(self, text):
        if not isinstance(text, str):
            raise ValueError('演示输入必须是文字。')
        self._draft = text
        if self.quick.text is not None:
            self.quick.text.set(text)
            self.quick.entry.icursor(tk.END)

    def set_background(self, light: bool):
        # The host paints the stage. B's palette is identical on both backgrounds.
        self._light_background = bool(light)

    def diagnostics(self):
        if self._closed:
            return {'variant': 'B', 'closed': True, 'visible': False, 'global_hooks': 0}
        self.root.update_idletasks()
        input_window = self.quick.window
        return {
            'variant': 'B', 'mode': self._mode, 'area': self._area, 'closed': False,
            'visible': bool(self.overlay._visible or self.quick._visible),
            'background_light': self._light_background, 'scale': self.metrics.scale,
            'overlay_geometry': self.overlay.window.geometry(),
            'input_geometry': input_window.geometry() if input_window is not None else None,
            'overlay_visible': self.overlay._visible, 'input_visible': self.quick._visible,
            'overlay_class': type(self.overlay).__module__ + '.' + type(self.overlay).__name__,
            'input_class': type(self.quick).__module__ + '.' + type(self.quick).__name__,
            'original_layout_preserved': type(self.overlay) is overlay_window.OverlayWindow
                                         and type(self.quick) is quick_input.QuickInput,
            'layout_functions_overridden': [], 'font_and_spacing_overrides': [],
            'overlay_width_logical': self.settings.overlay_width,
            'overlay_font_size': self.settings.overlay_font_size,
            'panel_max_rows': len(self.overlay.rows), 'subtitle_max_messages': 1,
            'input_layout': _widget_layout(input_window) if input_window is not None else [],
            'overlay_layout': _widget_layout(self.overlay.window),
            'transparent_subtitle_unchanged': True, 'subtitle_foreground': '#ffffff',
            'subtitle_outline': '#141414', 'global_hooks': 0, 'game_access': False,
            'cloud_requests': 0, 'preferences_saved': False,
            'ime_attached': bool(self.quick._native and self.quick._native._hwnd),
            'pending_demo_callbacks': len(self.controller.timers), 'palette': dict(self.palette),
        }

    def close(self):
        if self._closed:
            return
        self.hide()
        self._closed = True
        self.controller.cancel_pending()
        try:
            self.quick.close()
        finally:
            self.overlay.close()
