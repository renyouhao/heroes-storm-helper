"""Open native A/B overlay trials with local demonstration data only.

This entry point does not start the assistant controller, game acquisition,
global hotkeys, a WebView, cloud services, or preference persistence.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from hots_chat.display_runtime import enable_process_dpi_awareness
from hots_chat.layout import display_metrics
from hots_chat.ui.theme import apply_theme
from hots_chat.ui.brand_icon import apply_assistant_icon
from hots_chat.window_settings import WindowSettings


INITIAL_DRAFT = '我们等大招好了再开团'
DEMO_MESSAGES = [
    {'sender': 'Raven', 'channel': '团队', 'time': '14:21',
     'original': 'Take the camp first, then rotate bot.', 'translated': '先拿营地，然后一起去下路。'},
    {'sender': 'Jin', 'channel': '团队', 'time': '14:22',
     'original': 'My ult is up in 10 seconds.', 'translated': '等我一下，大招还有 10 秒。'},
    {'sender': 'Mochi', 'channel': '团队', 'time': '14:22',
     'original': 'Got it. I will check vision.', 'translated': '收到，我先去看一下视野。'},
    {'sender': 'Raven', 'channel': '团队', 'time': '14:23',
     'original': 'Let them use their abilities first.', 'translated': '别急，等对面先交技能。'},
]
TRANSLATIONS = {
    INITIAL_DRAFT: ("Let's wait until our ult is ready, then start the fight.", '궁극기가 준비되면 한타를 시작해요.'),
    '先拿营地，然后一起去下路': ('Take the camp first, then rotate bot.', '먼저 용병 캠프를 점령하고 함께 아래로 가요.'),
    '等我一下，大招还有 10 秒': ('My ult is up in 10 seconds.', '잠깐만요. 궁극기 쿨타임이 10초 남았어요.'),
    '收到，我先去看一下视野': ('Got it. I will check vision.', '알겠어요. 먼저 시야를 확인할게요.'),
    '别急，等对面先交技能': ('Let them use their abilities first.', '서두르지 말고 상대가 먼저 기술을 쓰게 해요.'),
    '你好': ('Hello.', '안녕하세요.'),
}


def demo_translate(text: str, target: str) -> str:
    """A bounded local vocabulary, deliberately never a pretend cloud result."""
    if not text.strip():
        raise ValueError('请先输入中文。')
    if target not in ('en', 'ko'):
        raise ValueError('请选择英语或韩语。')
    key = text.strip().rstrip('。.!！')
    if key not in TRANSLATIONS:
        raise ValueError('离线预览仅支持演示短句；请从上方选择一句后试用。')
    return TRANSLATIONS[key][target == 'ko']


class OverlayComparison:
    BACKGROUND = '#F3F1FF'
    INK = '#191632'
    MUTED = '#716B8B'
    PURPLE = '#7547F5'

    def __init__(self, root: tk.Tk, *, variant='B', mode='input'):
        self.root = root
        self.closed = False
        self.errors = []
        self.previews = {}
        self.variant, self.mode = variant, mode
        self.light = False
        self.message_index = 2
        self._layout_timer = None
        self._stage_signature = None
        self._root_hidden = False
        self._transitioning = False
        self._last_area = None
        self.settings = WindowSettings(color_theme='lavender', hotkeys={})
        apply_theme(root, self.settings)
        self.metrics = display_metrics(root)
        self.scale = self.metrics.scale
        self.messages = [dict(item) for item in DEMO_MESSAGES[:3]]
        root.title('风暴英雄助手 · 悬浮窗 A/B 对比预览')
        root.report_callback_exception = self._callback_error
        apply_assistant_icon(root,default=True)
        left, top, right, bottom = self.metrics.work_area
        width = min(self.px(1240), right - left - self.px(40))
        height = min(self.px(810), bottom - top - self.px(70))
        root.geometry(f'{width}x{height}+{left + (right-left-width)//2}+{top + max(self.px(20),(bottom-top-height)//2)}')
        root.minsize(min(width, self.px(900)), min(height, self.px(650)))
        self.notice = tk.StringVar(root, value='预览已就绪。可切换 A/B，比较字幕、面板和输入条。')
        self.phrase = tk.StringVar(root, value=INITIAL_DRAFT)
        self._build()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Control-t>', self._open_input)
        root.bind('<Control-T>', self._open_input)
        root.bind('<Escape>', self._hide_current)
        root.bind('<Configure>', self._schedule_layout, add='+')
        root.bind('<Unmap>', self._root_unmapped, add='+')
        root.bind('<Map>', self._root_mapped, add='+')
        root.after_idle(self._layout)

    def px(self, value):
        return max(1, round(value * self.scale))

    def font(self, size, bold=False):
        return ('Microsoft YaHei UI', -self.px(size), 'bold' if bold else 'normal')

    def label(self, parent, text='', *, size=14, color=None, bold=False, **kwargs):
        return tk.Label(parent, text=text, background=self.BACKGROUND,
                        foreground=color or self.INK, font=self.font(size, bold), **kwargs)

    def button(self, parent, text, command, *, small=False):
        return tk.Button(parent, text=text, command=command, relief='flat', borderwidth=1,
                         background='#FFFFFF', foreground='#4B3D68',
                         activebackground='#E6DCFC', activeforeground='#4F24BF',
                         highlightbackground='#D8CDED', highlightthickness=1,
                         font=self.font(12 if small else 14),
                         padx=self.px(12 if small else 18), pady=self.px(8), cursor='hand2')

    def _build(self):
        head = tk.Frame(self.root, background=self.BACKGROUND)
        head.pack(fill='x', padx=self.px(24), pady=(self.px(20), self.px(14)))
        brand = tk.Canvas(head, width=self.px(38), height=self.px(42), background=self.BACKGROUND, highlightthickness=0)
        brand.pack(side='left', padx=(0, self.px(12)))
        brand.create_polygon(*[self.px(v) for v in (19,1,36,11,36,31,19,41,2,31,2,11)], fill=self.PURPLE, outline='')
        brand.create_arc(self.px(10),self.px(11),self.px(29),self.px(31),start=20,extent=290,outline='white',style='arc',width=self.px(3))
        titles = tk.Frame(head, background=self.BACKGROUND)
        titles.pack(side='left')
        self.label(titles, '悬浮窗方案对比', size=24, bold=True).pack(anchor='w')
        self.label(titles, '已采用 B 原样换配色；A 保留为对照预览。', size=12, color=self.MUTED).pack(anchor='w', pady=(self.px(4),0))
        self.label(head, '本地原生窗口 · 离线演示', size=12, color='#856BAA').pack(side='right', anchor='n', pady=self.px(7))

        choices = tk.Frame(self.root, background=self.BACKGROUND)
        choices.pack(fill='x', padx=self.px(24))
        self.variant_buttons = {}
        for key, text in (('A','A · 原型新样式'),('B','B · 原样换配色')):
            item = self.button(choices, text, lambda value=key:self.select_variant(value))
            item.pack(side='left', padx=(0,self.px(10)))
            self.variant_buttons[key] = item
        self.variant_description = self.label(choices, size=12, color=self.MUTED, anchor='w')
        self.variant_description.pack(side='left', padx=self.px(8), fill='x', expand=True)

        options = tk.Frame(self.root, background=self.BACKGROUND)
        options.pack(fill='x', padx=self.px(24), pady=(self.px(13),self.px(10)))
        self.mode_buttons = {}
        for key, text in (('subtitle','默认字幕'),('panel','消息面板'),('input','快捷输入')):
            item = self.button(options, text, lambda value=key:self.select_mode(value), small=True)
            item.pack(side='left', padx=(0,self.px(7)))
            self.mode_buttons[key] = item
        self.button(options, '下一条消息', self.next_message, small=True).pack(side='left', padx=(self.px(7),0))
        self.button(options, '恢复位置', self.restore_position, small=True).pack(side='right')
        self.background_button = self.button(options, '切换浅色背景', self.toggle_background, small=True)
        self.background_button.pack(side='right', padx=(0,self.px(8)))

        phrases = tk.Frame(self.root, background=self.BACKGROUND)
        phrases.pack(fill='x', padx=self.px(24), pady=(0,self.px(12)))
        self.label(phrases, '演示短句', size=12, color=self.MUTED).pack(side='left', padx=(0,self.px(10)))
        self.phrase_control = ttk.Combobox(phrases, state='readonly', textvariable=self.phrase,
                                          values=tuple(TRANSLATIONS), width=38, font=self.font(12))
        self.phrase_control.pack(side='left', fill='x', expand=True)
        self.button(phrases, '填入输入条', self.use_phrase, small=True).pack(side='left', padx=self.px(9))
        self.phrase_hint = self.label(phrases, '英 / 韩均可试用；任意文本不调用云翻译。', size=11, color=self.MUTED)
        self.phrase_hint.pack(side='right')

        self.stage = tk.Canvas(self.root, background='#213844', highlightthickness=1,
                               highlightbackground='#D7CFE8', borderwidth=0)
        self.stage.pack(fill='both', expand=True, padx=self.px(24))
        self.stage.bind('<Configure>', self._schedule_layout, add='+')

        bottom = tk.Frame(self.root, background=self.BACKGROUND)
        bottom.pack(fill='x', padx=self.px(24), pady=(self.px(11),self.px(16)))
        self.notice_label = self.label(bottom, textvariable=self.notice, size=12, color='#67547F', anchor='w')
        self.notice_label.pack(fill='x')
        self.label(bottom, '预览窗口内 Ctrl+T 呼出输入，Esc 收起。背景是示意画面；不读取游戏、不保存偏好。',
                   size=11, color='#8B7D9F', anchor='w').pack(fill='x', pady=(self.px(5),0))
        self._update_controls()

    def _update_controls(self):
        for key, button in self.variant_buttons.items():
            selected = key == self.variant
            button.configure(background=self.PURPLE if selected else '#FFFFFF',
                             foreground='#FFFFFF' if selected else '#4B3D68',
                             activebackground='#6532D9' if selected else '#E6DCFC',
                             activeforeground='#FFFFFF' if selected else '#4F24BF')
        for key, button in self.mode_buttons.items():
            selected = key == self.mode
            button.configure(background='#E3D8FC' if selected else '#FFFFFF',
                             foreground='#592AC3' if selected else '#4B3D68')
        self.variant_description.configure(text=('重排布局：浅紫标题、圆角面板、双栏回复。' if self.variant == 'A'
                                                 else '保留布局：原字幕、原面板、单行输入与语言下拉。'))

    def _preview(self):
        if self.variant not in self.previews:
            if self.variant == 'A':
                from overlay_previews.prd import PrdPreview
                factory = PrdPreview
            else:
                from overlay_previews.recolor import RecolorPreview
                factory = RecolorPreview
            self.previews[self.variant] = factory(self.root, metrics=self.metrics,
                                                  messages=self.messages, notify=self.notify,
                                                  translate=demo_translate)
        return self.previews[self.variant]

    def notify(self, text):
        if not self.closed:
            self.notice.set(str(text))

    def select_variant(self, variant):
        if self.closed:
            return
        if variant not in ('A','B'):
            raise ValueError('未知方案')
        if self.variant in self.previews and variant != self.variant:
            self.previews[self.variant].hide()
        self.variant = variant
        self._update_controls()
        self.notify(f'当前方案 {variant}。两版保留各自草稿，切换不会改动正式助手。')
        self._layout()

    def select_mode(self, mode):
        if mode not in ('subtitle','panel','input'):
            raise ValueError('未知预览状态')
        self.mode = mode
        self._update_controls()
        self._layout()

    def _open_input(self, event=None):
        self.select_mode('input')
        return 'break'

    def _hide_current(self, event=None):
        if self.variant in self.previews:
            self.previews[self.variant].hide()
        self.notify('浮窗已收起。点击上方任一显示方式可再次打开。')
        return 'break'

    def use_phrase(self):
        self.select_mode('input')
        preview = self._preview()
        if hasattr(preview,'set_draft'):
            preview.set_draft(self.phrase.get())
        self.notify('已填入演示短句。A：翻译后点复制；B：Enter 翻译并复制。')

    def next_message(self):
        self.message_index = (self.message_index + 1) % len(DEMO_MESSAGES)
        item = dict(DEMO_MESSAGES[self.message_index])
        self.messages = (self.messages + [item])[-3:]
        for preview in self.previews.values():
            preview.set_messages(self.messages)
        self.notify('已更新演示消息：' + item['sender'] + '，' + item['translated'])
        self._layout()

    def toggle_background(self):
        self.light = not self.light
        self.background_button.configure(text='切换深色背景' if self.light else '切换浅色背景')
        self._stage_signature = None
        for preview in self.previews.values():
            preview.set_background(self.light)
        self._layout()

    def restore_position(self):
        preview = self._preview()
        preview.hide()
        self._layout()
        self.notify('已恢复预览位置。')

    def _schedule_layout(self, event=None):
        if self.closed or event is not None and event.widget not in (self.root,self.stage):
            return
        if self._layout_timer is not None:
            self.root.after_cancel(self._layout_timer)
        self._layout_timer = self.root.after(90,self._layout)

    def _layout(self):
        if self.closed:
            return
        if self._layout_timer is not None:
            self.root.after_cancel(self._layout_timer)
            self._layout_timer = None
        self.root.update_idletasks()
        if self.root.state() == 'iconic' or self.stage.winfo_width() < 50:
            return
        if self.root.winfo_width() < self.px(1080):
            self.phrase_hint.pack_forget()
        elif not self.phrase_hint.winfo_manager():
            self.phrase_hint.pack(side='right')
        self.variant_description.configure(wraplength=max(self.px(200),self.root.winfo_width()-self.px(470)))
        self._draw_stage()
        area = (self.stage.winfo_rootx()+self.px(4), self.stage.winfo_rooty()+self.px(4),
                self.stage.winfo_width()-self.px(8), self.stage.winfo_height()-self.px(8))
        self._last_area = area
        preview = self._preview()
        preview.set_background(self.light)
        preview.show(self.mode,area)

    def _draw_stage(self):
        canvas = self.stage
        width,height = canvas.winfo_width(),canvas.winfo_height()
        signature = width,height,self.light
        if signature == self._stage_signature:
            return
        self._stage_signature = signature
        canvas.delete('all')
        if self.light:
            ground,river,road,edge,stone,forest,caption = '#A8B9B7','#92B8CA','#C1C0AC','#8D9C97','#9AA8AD','#78978C','#425C64'
        else:
            ground,river,road,edge,stone,forest,caption = '#29454C','#365E75','#546D6D','#253F49','#475D6C','#335B56','#91A9B6'
        canvas.configure(background=ground)
        def points(*values):
            return [value * (width if i%2==0 else height) for i,value in enumerate(values)]
        canvas.create_line(*points(-.1,.64,.19,.28,.38,.44,.65,.17,1.1,.35),smooth=True,width=height*.19,fill=edge)
        canvas.create_line(*points(-.1,.64,.19,.28,.38,.44,.65,.17,1.1,.35),smooth=True,width=height*.12,fill=river)
        canvas.create_line(*points(-.05,.8,.23,.9,.45,.54,.72,.7,1.1,.75),smooth=True,width=height*.19,fill=edge)
        canvas.create_line(*points(-.05,.8,.23,.9,.45,.54,.72,.7,1.1,.75),smooth=True,width=height*.12,fill=road)
        canvas.create_line(*points(.15,-.1,.36,.35,.58,.65,.69,1.1),smooth=True,width=height*.16,fill=edge)
        canvas.create_line(*points(.15,-.1,.36,.35,.58,.65,.69,1.1),smooth=True,width=height*.10,fill=road)
        for x,y,s in ((.04,.18,.09),(.1,.08,.1),(.16,.22,.08),(.80,.44,.1),(.87,.36,.12),(.95,.49,.1),(.69,.02,.07)):
            cx,cy,size = x*width,y*height,s*height
            canvas.create_oval(cx-size*.55,cy+size*.9,cx+size*.55,cy+size*1.2,fill=edge,outline='')
            canvas.create_polygon(cx,cy,cx-size*.5,cy+size*.65,cx-size*.3,cy+size*.65,cx-size*.65,cy+size,cx+size*.65,cy+size,cx+size*.3,cy+size*.65,cx+size*.5,cy+size*.65,fill=forest,outline='')
        for x,y,s in ((.43,.12,.15),(.53,.13,.11),(.2,.55,.09),(.9,.15,.1)):
            cx,cy,size=x*width,y*height,s*height
            canvas.create_polygon(cx-size*.5,cy+size*.5,cx-size*.2,cy,cx+size*.35,cy+size*.05,cx+size*.6,cy+size*.55,cx+size*.2,cy+size*.85,cx-size*.35,cy+size*.8,fill=stone,outline='')
        cx,cy=width*.63,height*.36
        canvas.create_oval(cx-height*.06,cy+height*.03,cx+height*.06,cy+height*.1,fill=edge,outline='')
        canvas.create_polygon(cx,cy-height*.09,cx-height*.025,cy-height*.02,cx-height*.025,cy+height*.05,cx,cy+height*.08,cx+height*.04,cy+height*.04,cx+height*.025,cy-height*.03,fill='#8D81AF',outline='')
        for x,y,color in ((.48,.53,'#79B5CF'),(.53,.58,'#B08FB1')):
            cx,cy=x*width,y*height
            radius=self.px(12)
            canvas.create_oval(cx-radius,cy-radius,cx+radius,cy+radius,fill=road,outline=color,width=self.px(2))
            canvas.create_line(cx-self.px(16),cy-self.px(21),cx+self.px(12),cy-self.px(21),fill=color,width=self.px(3))
        canvas.create_text(self.px(20),self.px(18),text='背景仅为游戏画面示意',anchor='nw',font=self.font(11),fill=caption)
        canvas.create_text(width-self.px(20),self.px(18),text='16    14:23    16',anchor='ne',font=self.font(12,True),fill=caption)
        canvas.create_text(width-self.px(20),height-self.px(18),text='可拖动输入标题栏 · 点击“恢复位置”复原',anchor='se',font=self.font(11),fill=caption)

    def _root_unmapped(self, event):
        if event.widget is self.root and self.root.state() == 'iconic':
            self._root_hidden = True
            for preview in self.previews.values():
                preview.hide()

    def _root_mapped(self, event):
        if event.widget is self.root and self._root_hidden:
            self._root_hidden = False
            self._schedule_layout()

    def _callback_error(self, exception_type, exception, tb):
        detail=''.join(traceback.format_exception(exception_type,exception,tb))
        self.errors.append(detail)
        print(detail,file=sys.stderr)
        self.notify('预览出现错误：' + str(exception))

    def diagnostics(self):
        return {'variant':self.variant,'mode':self.mode,'stage':self._last_area,
                'light':self.light,'errors':list(self.errors),
                'previews':{key:value.diagnostics() for key,value in self.previews.items()}}

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self._layout_timer is not None:
            self.root.after_cancel(self._layout_timer)
            self._layout_timer = None
        for preview in self.previews.values():
            preview.close()
        self.previews.clear()
        self.root.destroy()


def run_smoke_test(app, output):
    """Exercise this preview's own public methods, without synthesizing input."""
    steps = [(variant,mode,light) for variant in ('A','B')
             for light in (False,True) for mode in ('subtitle','panel','input')]
    records=[]
    def capture():
        records.append(app.diagnostics())
        next_step()
    def next_step():
        if steps:
            variant,mode,light=steps.pop(0)
            try:
                app.select_variant(variant)
                if app.light != light:
                    app.toggle_background()
                app.select_mode(mode)
                app.root.after(160,capture)
            except Exception:
                app.errors.append(traceback.format_exc())
                finish()
        else:
            finish()
    def finish():
        report={'created':datetime.now().astimezone().isoformat(),
                'scope':'Offline native visual preview; no game, cloud, saved preferences, global hotkeys or WebView.',
                'states':records,'errors':list(app.errors)}
        app.close()
        report['closed']=True
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'states':len(records),'errors':len(report['errors']),'output':str(output)},ensure_ascii=False))
        if report['errors'] or len(records)!=12:
            raise SystemExit(1)
    app.root.after(200,next_step)


def main():
    parser=argparse.ArgumentParser(description='风暴英雄助手悬浮窗 A/B 原生预览')
    parser.add_argument('--variant',choices=('A','B'),default='B')
    parser.add_argument('--mode',choices=('subtitle','panel','input'),default='input')
    parser.add_argument('--smoke-test',action='store_true')
    parser.add_argument('--smoke-output',type=Path,default=ROOT/'artifacts'/'悬浮窗AB预览自检.json')
    args=parser.parse_args()
    enable_process_dpi_awareness()
    root=tk.Tk()
    app=OverlayComparison(root,variant=args.variant,mode=args.mode)
    if args.smoke_test:
        run_smoke_test(app,args.smoke_output)
    root.mainloop()


if __name__=='__main__':
    main()
