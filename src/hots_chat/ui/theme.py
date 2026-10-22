"""三种桌面主题与独立的游戏内深色表面，所有控件共用语义颜色。"""
import ctypes
import sys
from tkinter import ttk

from ..appearance import DEFAULT_THEME, THEME_NAMES, normalize_theme


PALETTES = {
    'moonlight': {
        'background': '#F4F6FA', 'surface': '#FFFFFF', 'panel': '#FFFFFF',
        'field': '#FAFBFE', 'text': '#232B3C', 'muted': '#596477',
        'accent': '#4E5ED2', 'on_accent': '#FFFFFF', 'secondary': '#5164AB',
        'success': '#21734E', 'warning': '#865B12', 'danger': '#B42C48',
        'border': '#D6DDE8', 'selection': '#E4E9FF',
    },
    'graphite': {
        'background': '#17191F', 'surface': '#232630', 'panel': '#20232C',
        'field': '#1C1F27', 'text': '#F1F2F7', 'muted': '#A5ACBD',
        'accent': '#B8A4F4', 'on_accent': '#222033', 'secondary': '#A99BCB',
        'success': '#82D6AF', 'warning': '#E7BE7C', 'danger': '#FF9BAF',
        'border': '#3E4556', 'selection': '#3B3550',
    },
    'mint': {
        'background': '#EAF3F1', 'surface': '#FCFEFD', 'panel': '#FCFEFD',
        'field': '#F4FAF7', 'text': '#263D39', 'muted': '#526D65',
        'accent': '#246D60', 'on_accent': '#FFFFFF', 'secondary': '#3A7669',
        'success': '#286A49', 'warning': '#805A14', 'danger': '#B2394D',
        'border': '#C2D9D1', 'selection': '#D7EAE5',
    },
}
PALETTE_NAMES = THEME_NAMES
COLORS = PALETTES[DEFAULT_THEME]


def theme_colors(settings=DEFAULT_THEME):
    value = settings if isinstance(settings, str) else getattr(settings, 'color_theme', DEFAULT_THEME)
    return PALETTES[normalize_theme(value)]


def overlay_colors(settings=DEFAULT_THEME):
    """亮色桌面主题也使用暗色游戏浮层，避免战场背景透出后降低可读性。"""
    value = settings if isinstance(settings, str) else getattr(settings, 'color_theme', DEFAULT_THEME)
    name = normalize_theme(value)
    return {
        'background': '#181C24', 'surface': '#222732', 'panel': '#222732',
        'field': '#212631', 'text': '#F5F7FC', 'muted': '#BCC4D3',
        'accent': {'moonlight': '#A7B5FF', 'graphite': '#B8A4F4', 'mint': '#84D7C7'}[name],
        'on_accent': '#1D2430', 'secondary': '#AAB5C8',
        'success': '#82D6AF', 'warning': '#E7BE7C', 'danger': '#FF9BAF',
        'border': '#465264', 'selection': '#344156',
    }


def _configure_controls(style, c, prefix=''):
    def key(name):
        return prefix + name
    style.configure(key('TFrame'), background=c['background'])
    style.configure(key('TLabel'), background=c['background'], foreground=c['text'])
    style.configure(key('Muted.TLabel'), background=c['background'], foreground=c['muted'])
    style.configure(key('TButton'), background=c['surface'], foreground=c['text'],
                    bordercolor=c['border'], lightcolor=c['surface'], darkcolor=c['surface'],
                    focuscolor=c['accent'], padding=(12, 7), relief='flat')
    style.map(key('TButton'), background=[('disabled', c['field']), ('pressed', c['selection']),
                                        ('active', c['selection'])],
              foreground=[('disabled', c['muted'])])
    style.configure(key('Accent.TButton'), background=c['accent'], foreground=c['on_accent'],
                    bordercolor=c['accent'], lightcolor=c['accent'], darkcolor=c['accent'],
                    focuscolor=c['on_accent'], padding=(12, 7))
    style.map(key('Accent.TButton'),
              background=[('disabled', c['surface']), ('pressed', c['accent']), ('active', c['accent'])],
              foreground=[('disabled', c['muted']), ('!disabled', c['on_accent'])])
    for widget in ('TCheckbutton', 'TRadiobutton'):
        style.configure(key(widget), background=c['background'], foreground=c['text'],
                        indicatorbackground=c['field'], indicatorforeground=c['accent'],
                        focuscolor=c['accent'], padding=(2, 4))
        style.map(key(widget), background=[('active', c['background'])],
                  indicatorbackground=[('selected', c['accent'])],
                  indicatorforeground=[('selected', c['on_accent'])],
                  foreground=[('disabled', c['muted'])])
    for widget in ('TEntry', 'TCombobox', 'TSpinbox'):
        style.configure(key(widget), fieldbackground=c['field'], foreground=c['text'],
                        background=c['surface'], arrowcolor=c['text'], insertcolor=c['accent'],
                        selectbackground=c['selection'], selectforeground=c['text'],
                        bordercolor=c['border'], lightcolor=c['field'], darkcolor=c['field'], padding=6)
        style.map(key(widget), fieldbackground=[('readonly', c['field']), ('disabled', c['surface'])],
                  background=[('disabled', c['surface']), ('readonly', c['surface']),
                              ('pressed', c['selection']), ('active', c['surface'])],
                  foreground=[('disabled', c['muted']), ('readonly', c['text'])],
                  selectbackground=[('focus', c['selection']), ('!focus', c['selection'])],
                  selectforeground=[('focus', c['text']), ('!focus', c['text'])],
                  lightcolor=[('focus', c['accent'])], darkcolor=[('focus', c['accent'])],
                  bordercolor=[('focus', c['accent'])])


def apply_theme(root, theme=DEFAULT_THEME):
    """同步已创建的窗口；不重建输入、控制器、消息历史或异步任务。"""
    c = theme_colors(theme)
    overlay = overlay_colors(theme)
    root.configure(background=c['background'])
    for widget_class in ('Text', 'Listbox', 'Entry'):
        for option, value in (
            ('background', c['field']), ('foreground', c['text']),
            ('insertBackground', c['accent']), ('selectBackground', c['selection']),
            ('selectForeground', c['text']), ('highlightBackground', c['border']),
            ('highlightColor', c['accent']), ('highlightThickness', 1), ('relief', 'flat'),
        ):
            root.option_add(f'*{widget_class}.{option}', value)
    root.option_add('*Toplevel.background', c['background'])
    root.option_add('*TCombobox*Listbox.background', c['field'])
    root.option_add('*TCombobox*Listbox.foreground', c['text'])
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', background=c['background'], foreground=c['text'],
                    bordercolor=c['border'], troughcolor=c['field'],
                    selectbackground=c['selection'], selectforeground=c['text'], font='TkDefaultFont')
    style.map('.', selectbackground=[('!focus', c['selection'])],
              selectforeground=[('!focus', c['text'])])
    _configure_controls(style, c)
    _configure_controls(style, overlay, 'Overlay.')
    for family in ('Surface', 'Panel', 'Toolbar'):
        style.configure(f'{family}.TFrame', background=c['surface'])
        style.configure(f'{family}.TLabel', background=c['surface'], foreground=c['text'])
        style.configure(f'{family}.Muted.TLabel', background=c['surface'], foreground=c['muted'])
        style.configure(f'{family}.TRadiobutton', background=c['surface'], foreground=c['text'],
                        indicatorbackground=c['field'], indicatorforeground=c['accent'],
                        focuscolor=c['accent'], padding=(2, 4))
        style.map(f'{family}.TRadiobutton', background=[('active', c['surface'])],
                  indicatorbackground=[('selected', c['accent'])],
                  indicatorforeground=[('selected', c['on_accent'])],
                  foreground=[('disabled', c['muted'])])
    style.configure('Heading.TLabel', foreground=c['text'], font=('Microsoft YaHei UI', 14, 'bold'))
    style.configure('Brand.TLabel', background=c['surface'], foreground=c['text'],
                    font=('Microsoft YaHei UI', 12, 'bold'))
    style.configure('PageTitle.TLabel', background=c['background'], foreground=c['text'],
                    font=('Microsoft YaHei UI', 17, 'bold'))
    style.configure('ComposerTitle.TLabel', background=c['surface'], foreground=c['text'],
                    font=('Microsoft YaHei UI', 10, 'bold'))
    style.configure('Sidebar.TButton', background=c['surface'], foreground=c['muted'],
                    padding=(12,12), anchor='w', borderwidth=0, relief='flat')
    style.map('Sidebar.TButton', background=[('active',c['selection']),('pressed',c['selection'])],
              foreground=[('active',c['text'])])
    style.configure('Sidebar.Selected.TButton', background=c['selection'], foreground=c['text'],
                    padding=(12,12), anchor='w', borderwidth=0, relief='flat')
    style.map('Sidebar.Selected.TButton', background=[('active',c['selection']),('pressed',c['selection'])],
              foreground=[('active',c['text'])])
    style.configure('Nav.TButton', background=c['background'], foreground=c['muted'],
                    relief='flat', padding=(14, 10), borderwidth=0)
    style.map('Nav.TButton', background=[('pressed', c['selection']), ('active', c['selection'])],
              foreground=[('active', c['text'])])
    style.configure('TLabelframe', background=c['background'], bordercolor=c['border'])
    style.configure('TLabelframe.Label', background=c['background'], foreground=c['muted'])
    style.configure('TNotebook', background=c['background'], borderwidth=0)
    style.configure('TNotebook.Tab', background=c['background'], foreground=c['muted'],
                    padding=(16, 9), borderwidth=0)
    style.map('TNotebook.Tab', background=[('selected', c['surface']), ('active', c['selection'])],
              foreground=[('selected', c['text']), ('active', c['text'])])
    style.configure('Treeview', background=c['field'], fieldbackground=c['field'],
                    foreground=c['text'], borderwidth=0)
    style.map('Treeview', background=[('selected', c['selection'])], foreground=[('selected', c['text'])])
    style.configure('Treeview.Heading', background=c['surface'], foreground=c['muted'],
                    bordercolor=c['surface'], relief='flat', padding=(8, 6))
    style.map('Treeview.Heading', background=[('active', c['selection'])])
    for widget in ('Vertical.TScrollbar', 'Horizontal.TScrollbar'):
        style.configure(widget, background=c['surface'], arrowcolor=c['muted'],
                        bordercolor=c['background'], troughcolor=c['field'])
    style.configure('TSeparator', background=c['border'])
    style.configure('Horizontal.TScale', background=c['background'], troughcolor=c['field'])
    pending = [(root, 'main')]
    while pending:
        widget, scope = pending.pop()
        scope = getattr(widget, '_hots_theme_scope', scope)
        colors = overlay if scope == 'overlay' else c
        pending.extend((child, scope) for child in widget.winfo_children())
        kind = widget.winfo_class()
        if kind in ('Text', 'Listbox', 'Entry'):
            options = dict(background=colors['field'], foreground=colors['text'],
                           selectbackground=colors['selection'], selectforeground=colors['text'],
                           highlightbackground=colors['border'], highlightcolor=colors['accent'])
            if kind != 'Listbox':
                options['insertbackground'] = colors['accent']
            widget.configure(**options)
        elif kind == 'Toplevel':
            widget.configure(background=colors['background'])
            if scope != 'overlay':
                style_native_window(widget, colors)
    style_native_window(root, c)
    return style


def style_native_window(window, colors=None):
    """Windows 支持时同步标题栏明暗、文字和圆角；不改变系统主题。"""
    if sys.platform != 'win32':
        return False
    try:
        from ctypes import wintypes
        window.update_idletasks()
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        hwnd = user.GetAncestor(window.winfo_id(), 2)
        if not hwnd:
            return False
        function = ctypes.WinDLL('dwmapi').DwmSetWindowAttribute
        function.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        function.restype = ctypes.c_long
        colors = colors or COLORS
        def colorref(color):
            return int(color[5:7] + color[3:5] + color[1:3], 16)
        dark = sum(int(colors['background'][i:i + 2], 16) for i in (1, 3, 5)) < 384
        values = ((20, int(dark)), (33, 2), (35, colorref(colors['surface'])),
                  (36, colorref(colors['text'])))
        supported = False
        for attribute, value in values:
            data = wintypes.DWORD(value)
            supported |= function(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)) == 0
        return supported
    except (OSError, AttributeError):
        return False
