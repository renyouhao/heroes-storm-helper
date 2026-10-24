"""桌面与游戏浮层共用语义颜色；柔雾紫实体浮层跟随主界面。"""
import ctypes
import math
import struct
import sys
import tkinter as tk
import zlib
from tkinter import ttk

from ..appearance import DEFAULT_THEME, THEME_NAMES, normalize_theme


PALETTES = {
    'lavender': {
        'background': '#F3F1FF', 'surface': '#FFFFFF', 'panel': '#FFFFFF',
        'field': '#F5F4FA', 'text': '#191632', 'muted': '#716B8B',
        'accent': '#7547F5', 'on_accent': '#FFFFFF', 'secondary': '#696184',
        'success': '#438B40', 'warning': '#936C1D', 'danger': '#B63C5B',
        'border': '#E2DDF2', 'selection': '#F0EAFF', 'hover': '#F4F0FF',
        'sidebar': '#EEEBFC', 'sidebar_text': '#393253', 'sidebar_muted': '#6D6788',
        'sidebar_hover': '#E4DAFF',
    },
}
PALETTE_NAMES = THEME_NAMES
COLORS = PALETTES[DEFAULT_THEME]


def theme_colors(settings=DEFAULT_THEME):
    value = settings if isinstance(settings, str) else getattr(settings, 'color_theme', DEFAULT_THEME)
    return PALETTES[normalize_theme(value)]


def overlay_colors(settings=DEFAULT_THEME):
    """实体浮层跟随柔雾紫；返回副本，避免局部样式改动主调色板。"""
    return dict(theme_colors(settings))


_CHECKBOX_STATES = (
    ('disabled_alternate', ('disabled', 'alternate')),
    ('disabled_selected', ('disabled', 'selected')),
    ('disabled', ('disabled',)),
    ('pressed_alternate', ('pressed', 'alternate')),
    ('pressed_selected', ('pressed', 'selected')),
    ('pressed', ('pressed',)),
    ('focus_alternate', ('focus', 'alternate')),
    ('focus_selected', ('focus', 'selected')),
    ('focus', ('focus',)),
    ('active_alternate', ('active', 'alternate')),
    ('active_selected', ('active', 'selected')),
    ('active', ('active',)),
    ('alternate', ('alternate',)),
    ('selected', ('selected',)),
)


def _mix(first, second, amount):
    values = [round(int(first[i:i + 2], 16) * (1 - amount)
                    + int(second[i:i + 2], 16) * amount) for i in (1, 3, 5)]
    return '#{:02X}{:02X}{:02X}'.format(*values)


def _checkbox_pixels(size, colors, state):
    """以几何笔画绘制勾和横线，不依赖系统字体或 clam 的 X 图元。"""
    scale = size / 20
    disabled = state.startswith('disabled')
    selected = state.endswith('selected') or state.endswith('alternate')
    border = _mix(colors['muted'], colors['field'], .5) if disabled else colors['muted']
    fill = colors['field']
    mark = colors['on_accent']
    if selected:
        border = fill = _mix(colors['accent'], colors['field'], .6) if disabled else colors['accent']
        if disabled:
            mark = colors['text']
    if state.startswith('pressed'):
        fill = _mix(fill, colors['text'], .15)
        border = colors['accent']
    elif state.startswith('active'):
        fill = _mix(fill, colors['accent'], .12)
        border = colors['accent']

    def inside_box(x, y, inset, radius):
        low, high = inset, 20 - inset
        if not (low <= x <= high and low <= y <= high):
            return False
        cx = min(max(x, low + radius), high - radius)
        cy = min(max(y, low + radius), high - radius)
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2

    def on_segment(x, y, first, second):
        dx, dy = second[0] - first[0], second[1] - first[1]
        part = max(0, min(1, ((x - first[0]) * dx + (y - first[1]) * dy) / (dx * dx + dy * dy)))
        return math.hypot(x - first[0] - part * dx, y - first[1] - part * dy) <= .9

    pixels = bytearray()
    for py in range(size):
        pixels.append(0)  # PNG 行过滤器：无过滤。
        for px in range(size):
            x, y = px / scale, py / scale
            color = None
            if inside_box(x, y, 3, 2):
                color = fill if inside_box(x, y, 4, 1) else border
            if selected and color is not None:
                if state.endswith('alternate'):
                    if 6 <= x <= 14 and 9.2 <= y <= 10.8:
                        color = mark
                elif (on_segment(x, y, (6, 10), (9, 13))
                      or on_segment(x, y, (9, 13), (14, 7))):
                    color = mark
            if state.startswith('focus') and inside_box(x, y, .5, 3):
                if not inside_box(x, y, 1.5, 2):
                    color = colors['accent']
            if color is None:
                pixels.extend((0, 0, 0, 0))
            else:
                pixels.extend(int(color[i:i + 2], 16) for i in (1, 3, 5))
                pixels.append(255)

    def chunk(kind, content):
        return (struct.pack('!I', len(content)) + kind + content
                + struct.pack('!I', zlib.crc32(kind + content)))

    # 标准库编码透明 PNG，一次传入 Tk，避免逐像素跨 Tcl 调用。
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('!2I5B', size, size, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


def _switch_pixels(size, colors, state):
    """透明背景的胶囊开关；保留选中、禁用和键盘焦点的独立反馈。"""
    scale = size / 20
    width, height = round(44 * scale), round(26 * scale)
    disabled = state.startswith('disabled')
    selected = state.endswith(('selected', 'alternate'))
    track = colors['accent'] if selected else _mix(colors['muted'], colors['field'], .65)
    if disabled:
        track = _mix(track, colors['field'], .55)
    elif state.startswith('pressed'):
        track = _mix(track, colors['text'], .12)
    elif state.startswith('active'):
        track = _mix(track, colors['accent'], .15)
    thumb = '#FFFFFF' if selected else colors['surface']

    def capsule(x, y, inset):
        radius = 13 - inset
        center_x = min(max(x, 13), 31)
        return max(0, min(1, (.5 / scale - math.hypot(x - center_x, y - 13)
                             + radius) * scale))

    def rgb(color):
        return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))

    def pixel(px, py):
        x, y = px / scale, py / scale
        coverage = capsule(x, y, 3)
        color = track
        if state.startswith('focus'):
            ring = capsule(x, y, .5) - capsule(x, y, 1.5)
            if ring > coverage:
                coverage, color = ring, colors['accent']
        ball = max(0, min(1, (7.5 + .5 / scale
                              - math.hypot(x - (31 if selected else 13), y - 13)) * scale))
        if ball:
            # 球体在轨道内部，边缘按覆盖率与轨道混合即可。
            color = _mix(color, thumb, ball)
            coverage = max(coverage, ball)
        return (*rgb(color), round(255 * coverage))

    return _rgba_png(width, height, pixel)


class _CheckboxAssets:
    """每个 Tk 解释器持有固定图片，换肤/缩放原地更新，避免缓存增长。"""
    def __init__(self, scope, *, switch=False):
        # 资产不能反向持有根窗口，否则 root -> assets -> root 会推迟到任意线程 GC。
        self.element = f'Hots.{scope}.{"Switch" if switch else "Checkbutton"}.indicator'
        self.render = _switch_pixels if switch else _checkbox_pixels
        self.images = {}
        self.signature = None

    def update(self, style, colors, size):
        signature = (size, *(colors[role] for role in (
            'background', 'field', 'text', 'muted', 'border', 'accent', 'on_accent')))
        if self.signature != signature:
            for name in ('normal', *(name for name, _ in _CHECKBOX_STATES)):
                pixels = self.render(size, colors, name)
                image = self.images.get(name)
                if image is None:
                    image = self.images[name] = tk.PhotoImage(master=style.master, data=pixels, format='png')
                else:
                    image.configure(data=pixels, format='png')
            self.signature = signature
        if self.element not in style.element_names():
            states = [(*states, self.images[name]) for name, states in _CHECKBOX_STATES]
            style.element_create(self.element, 'image', self.images['normal'], *states,
                                 border=0, sticky='')
        return self.element

    def dispose(self):
        """在根窗口的 Destroy 事件中同步释放 Tcl 图片及解释器引用。"""
        for image in self.images.values():
            try:
                if image.name:
                    image.tk.call('image', 'delete', image.name)
            except tk.TclError:
                # 图元已由 Tcl 删除时，仍需断开外部 Python 图片持有的解释器引用。
                pass
            finally:
                image.name = None
                image.tk = None
        self.images.clear()
        self.signature = None


def _release_checkbox_images(event):
    owner = event.widget
    if isinstance(owner, tk.Tk):
        assets = owner.__dict__.pop('_hots_checkbox_assets', {})
        for asset in assets.values():
            asset.dispose()
        assets.clear()


def _configure_checkbutton_images(root, style, main, overlay):
    owner = root._root()
    assets = getattr(owner, '_hots_checkbox_assets', None)
    if assets is None:
        assets = owner._hots_checkbox_assets = {}
        owner.bind('<Destroy>', _release_checkbox_images, add='+')
    scale = max(.75, min(4, float(root.tk.call('tk', 'scaling')) * 72 / 96))
    size = round(20 * scale)
    for scope, colors, prefixes in (
            ('main', main, ('', 'Surface.', 'Panel.', 'Toolbar.')),
            ('overlay', overlay, ('Overlay.',))):
        if scope not in assets:
            assets[scope] = _CheckboxAssets(scope)
        element = assets[scope].update(style, colors, size)
        for prefix in prefixes:
            style.layout(prefix + 'TCheckbutton', [
                ('Checkbutton.padding', {'sticky': 'nswe', 'children': [
                    (element, {'side': 'left', 'sticky': ''}),
                    ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
                        ('Checkbutton.label', {'sticky': 'nswe'})]})]})])
    if 'switch' not in assets:
        assets['switch'] = _CheckboxAssets('main', switch=True)
    element = assets['switch'].update(style, main, size)
    for prefix in ('', 'Surface.', 'Panel.', 'Toolbar.'):
        style.layout(prefix + 'Switch.TCheckbutton', [
            ('Checkbutton.padding', {'sticky': 'nswe', 'children': [
                (element, {'side': 'left', 'sticky': ''}),
                ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
                    ('Checkbutton.label', {'sticky': 'nswe'})]})]})])


def refresh_theme_scale(root, theme=DEFAULT_THEME):
    """DPI 变化只刷新主题图片；已有图片和控件均原地复用。"""
    style = ttk.Style(root)
    if style.theme_use() == 'clam':
        _configure_checkbutton_images(root, style, theme_colors(theme), overlay_colors(theme))
        _configure_rounded_controls(root, style, theme_colors(theme), overlay_colors(theme))


_ROUNDED_BUTTON_STATES = (
    ('disabled', ('disabled',)), ('pressed_focus', ('pressed', 'focus')),
    ('active_focus', ('active', 'focus')), ('focus', ('focus',)),
    ('pressed', ('pressed',)), ('active', ('active',)),
)
_ROUNDED_BUTTON_STYLES = {
    'button': 'TButton', 'accent': 'Accent.TButton', 'nav': 'Nav.TButton',
    'sidebar': 'Sidebar.TButton', 'sidebar_selected': 'Sidebar.Selected.TButton',
    'overlay_button': 'Overlay.TButton', 'overlay_accent': 'Overlay.Accent.TButton',
    'surface_button': 'Surface.TButton', 'surface_accent': 'Surface.Accent.TButton',
    'surface_nav': 'Surface.Nav.TButton',
    'soft': 'Soft.TButton', 'surface_soft': 'Surface.Soft.TButton',
}


def _rgba_png(width, height, pixel):
    data = bytearray()
    for y in range(height):
        data.append(0)
        for x in range(width):
            data.extend(pixel(x + .5, y + .5))

    def chunk(kind, content):
        return (struct.pack('!I', len(content)) + kind + content
                + struct.pack('!I', zlib.crc32(kind + content)))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(data)) + chunk(b'IEND', b''))


def _rounded_png(radius, fill, border, stroke, backdrop):
    """抗锯齿圆角九宫格；角区覆盖 ttk 预先铺好的矩形填充。"""
    # Tk 8.6 的 image element 平铺中心区，不会把 1px 中心拉伸到控件大小。
    # 保留 16px 中心，避免宽输入框退化为每像素一次 GDI 绘制。
    size = radius * 2 + 16
    fill = tuple(int(fill[i:i + 2], 16) for i in (1, 3, 5))
    border = tuple(int(border[i:i + 2], 16) for i in (1, 3, 5))
    backdrop = tuple(int(backdrop[i:i + 2], 16) for i in (1, 3, 5))

    def coverage(x, y, inset):
        half = size / 2 - inset
        r = max(0, radius - inset)
        qx = abs(x - size / 2) - (half - r)
        qy = abs(y - size / 2) - (half - r)
        distance = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
        return max(0, min(1, .5 - distance))

    def pixel(x, y):
        outer = coverage(x, y, 0)
        if not outer:
            return (*backdrop, 255)
        inner = min(outer, coverage(x, y, stroke)) if stroke else outer
        color = tuple(round(f * inner + b * (outer - inner) + outside * (1 - outer))
                      for f, b, outside in zip(fill, border, backdrop))
        return (*color, 255)

    return _rgba_png(size, size, pixel)


def _arrow_png(scale, color, up):
    width, height = max(1, round(14 * scale)), max(1, round(10 * scale))
    color = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))

    def pixel(x, y):
        x, y = x / scale, y / scale
        if up:
            y = 10 - y
        inside = 3 <= y <= 7 and abs(x - 7) <= (7 - y) * .75
        return (*color, 255) if inside else (0, 0, 0, 0)

    return _rgba_png(width, height, pixel)


def _rounded_button_layout(element):
    return [(element, {'sticky': 'nswe', 'children': [
        ('Button.focus', {'sticky': 'nswe', 'children': [
            ('Button.padding', {'sticky': 'nswe', 'children': [
                ('Button.label', {'sticky': 'nswe'})]})]})]})]


class _RoundedControlAssets:
    """固定图片池和有界半径图元；只保存图片与字符串，不回指 Tk 根窗口。"""
    def __init__(self):
        self.images = {}
        self.signatures = {}
        self.elements = {}
        self.aliases = {}
        self.aliases_scanned = False
        self.button_radius = 8

    def _image(self, master, key, pixels):
        if key in self.images:
            self.images[key].configure(data=pixels, format='png')
        else:
            self.images[key] = tk.PhotoImage(master=master, data=pixels, format='png')

    def rectangle(self, style, role, radius, specs, pixels, *, backdrop, field=False, framed=True):
        signature = (radius, tuple(specs), backdrop)
        if self.signatures.get(role) != signature:
            for name, states, fill, border, stroke in specs:
                key = (radius, fill, border, stroke, backdrop)
                if key not in pixels:
                    pixels[key] = _rounded_png(*key)
                self._image(style.master, role + ':' + name, pixels[key])
            self.signatures[role] = signature
        if role not in self.elements:
            self.elements[role] = {}
            normal = self.images[role + ':normal']
            states = [(*states, self.images[role + ':' + name]) for name, states, *_ in specs if states]
            # ttk image 图元的九宫格 border 创建后不可修改。一次注册完整半径范围，
            # DPI 切换只选择既有图元；所有半径共享同一组原地更新的图片。
            for r in range(8 if field else 6, 41 if field else 33):
                element = f'Hots.Rounded.{role}.R{r}'
                inset = max(1, round(r / (10 if field else 8))) if framed else 0
                style.element_create(element, 'image', normal, *states,
                                     border=r, padding=inset, width=r * 2 + 1,
                                     height=r * 2 + 1, sticky='nswe')
                self.elements[role][r] = element
        return self.elements[role][radius]

    def arrow(self, style, scope, scale, colors, *, up=False):
        role = scope + ('_up' if up else '_down')
        signature = (scale, colors['text'], colors['muted'])
        if self.signatures.get(role) != signature:
            for state, color in (('normal', colors['text']), ('disabled', colors['muted'])):
                self._image(style.master, role + ':' + state, _arrow_png(scale, color, up))
            self.signatures[role] = signature
        element = f'Hots.Rounded.{scope}.' + ('uparrow' if up else 'downarrow')
        if role not in self.elements:
            style.element_create(element, 'image', self.images[role + ':normal'],
                                 ('disabled', self.images[role + ':disabled']), sticky='')
            self.elements[role] = element
        return element

    def dispose(self):
        for image in self.images.values():
            try:
                if image.name:
                    image.tk.call('image', 'delete', image.name)
            except tk.TclError:
                pass
            finally:
                image.name = None
                image.tk = None
        self.images.clear()
        self.signatures.clear()
        self.elements.clear()
        self.aliases.clear()


def _release_rounded_images(event):
    if isinstance(event.widget, tk.Tk):
        assets = event.widget.__dict__.pop('_hots_rounded_assets', None)
        if assets is not None:
            assets.dispose()


def _rounded_alias(style, assets, name):
    if not name.startswith(('Repeat', 'Loot')):
        return
    role = ('helper_accent' if name.endswith('.Accent.TButton') else
            'helper_cancel' if name.endswith('.Cancel.TButton') else None)
    if role is not None:
        assets.aliases[name] = role
        style.layout(name, _rounded_button_layout(assets.elements[role][assets.button_radius]))


def _configure_rounded_controls(root, style, main, overlay):
    owner = root._root()
    assets = getattr(owner, '_hots_rounded_assets', None)
    if assets is None:
        assets = owner._hots_rounded_assets = _RoundedControlAssets()
        owner.bind('<Destroy>', _release_rounded_images, add='+')
    scale = max(.75, min(4, float(root.tk.call('tk', 'scaling')) * 72 / 96))
    button_radius, field_radius = round(8 * scale), round(10 * scale)
    assets.button_radius = button_radius
    stroke = max(1, round(scale))
    pixels = {}  # 单次应用里复用相同像素，应用结束即释放，不累计主题/DPI缓存。
    states = (('normal', ()), *_ROUNDED_BUTTON_STATES)
    for role, name in _ROUNDED_BUTTON_STYLES.items():
        framed = bool(int(style.lookup(name, 'borderwidth') or 0))
        specs = []
        for state, bits in states:
            focused = 'focus' in bits
            specs.append((state, bits, style.lookup(name, 'background', bits),
                          style.lookup(name, 'focuscolor' if focused else 'bordercolor', bits),
                          stroke if framed or focused else 0))
        backdrop = (main['surface'] if role.startswith('surface_') else
                    overlay['background'] if role.startswith('overlay_') else
                    main['sidebar'] if role.startswith('sidebar') else main['background'])
        element = assets.rectangle(style, role, button_radius, specs, pixels,
                                   backdrop=backdrop, framed=framed)
        style.layout(name, _rounded_button_layout(element))
        if role.startswith('surface_'):
            for family in ('Panel.', 'Toolbar.'):
                style.layout(name.replace('Surface.', family), _rounded_button_layout(element))
    # 胶囊选项保留 TRadiobutton 类绑定和变量，仅替换指示器与背景。
    for role, prefix, backdrop in (('segment', '', main['background']),
                                   ('surface_segment', 'Surface.', main['surface'])):
        name = prefix + 'Segment.TRadiobutton'
        specs = [(state, bits, style.lookup(name, 'background', bits),
                  main['accent'] if 'focus' in bits else style.lookup(name, 'background', bits),
                  stroke if 'focus' in bits else 0)
                 for state, bits in (('normal', ()), *_CHECKBOX_STATES)]
        radius = min(32, round(12 * scale))
        element = assets.rectangle(style, role, radius, specs, pixels, backdrop=backdrop, framed=False)
        style.layout(name, [(element, {'sticky': 'nswe', 'children': [
            ('Radiobutton.padding', {'sticky': 'nswe', 'children': [
                ('Radiobutton.focus', {'sticky': 'nswe', 'children': [
                    ('Radiobutton.label', {'sticky': 'nswe'})]})]})]})])
    # 两个辅助页覆盖了 Accent/Cancel 的填充；共享其各自的固定语义图片。
    for role in ('helper_accent', 'helper_cancel'):
        specs = []
        for state, bits in states:
            if role == 'helper_accent':
                fill = main['surface'] if 'disabled' in bits else main['accent']
                border = main['on_accent'] if 'focus' in bits else main['accent']
            else:
                fill = main['selection'] if 'active' in bits or 'pressed' in bits else main['surface']
                border = main['accent'] if 'focus' in bits else main['border']
            specs.append((state, bits, fill, border, stroke))
        assets.rectangle(style, role, button_radius, specs, pixels, backdrop=main['background'])
    for scope, prefix, colors, role, backdrop in (
            ('main', '', main, 'field', main['background']),
            ('main', 'Surface.', main, 'surface_field', main['surface']),
            ('overlay', 'Overlay.', overlay, 'overlay_field', overlay['background'])):
        specs = [(state, bits, style.lookup(prefix + 'TEntry', 'fieldbackground', bits),
                  style.lookup(prefix + 'TEntry', 'bordercolor', bits), stroke)
                 for state, bits in (('normal', ()), ('disabled', ('disabled',)), ('focus', ('focus',)))]
        element = assets.rectangle(style, role, field_radius, specs, pixels, backdrop=backdrop, field=True)
        down = assets.arrow(style, scope, scale, colors)
        up = assets.arrow(style, scope, scale, colors, up=True)
        style.layout(prefix + 'TEntry', [(element, {'sticky': 'nswe', 'children': [
            ('Entry.padding', {'sticky': 'nswe', 'children': [('Entry.textarea', {'sticky': 'nswe'})]})]})])
        style.layout(prefix + 'TCombobox', [(element, {'sticky': 'nswe', 'children': [
            (down, {'side': 'right', 'sticky': ''}),
            ('Combobox.padding', {'sticky': 'nswe', 'children': [('Combobox.textarea', {'sticky': 'nswe'})]})]})])
        style.layout(prefix + 'TSpinbox', [(element, {'sticky': 'nswe', 'children': [
            ('null', {'side': 'right', 'sticky': 'ns', 'children': [
                (up, {'side': 'top', 'sticky': 'e'}), (down, {'side': 'bottom', 'sticky': 'e'})]}),
            ('Spinbox.padding', {'sticky': 'nswe', 'children': [('Spinbox.textarea', {'sticky': 'nswe'})]})]})])
        if prefix == 'Surface.':
            for family in ('Panel.', 'Toolbar.'):
                for control in ('TEntry', 'TCombobox', 'TSpinbox'):
                    style.layout(family + control, style.layout(prefix + control))
    for name in assets.aliases:
        _rounded_alias(style, assets, name)
    if not assets.aliases_scanned and owner.winfo_children():
        pending = list(owner.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if widget.winfo_class() == 'TButton':
                _rounded_alias(style, assets, str(widget.cget('style')))
        assets.aliases_scanned = True


def _configure_controls(style, c, prefix=''):
    def key(name):
        return prefix + name
    style.configure(key('TFrame'), background=c['background'])
    style.configure(key('TLabel'), background=c['background'], foreground=c['text'])
    style.configure(key('Muted.TLabel'), background=c['background'], foreground=c['muted'])
    style.configure(key('TButton'), background=c['surface'], foreground=c['text'],
                    bordercolor=c['border'], lightcolor=c['surface'], darkcolor=c['surface'],
                    focuscolor=c['accent'], padding=(10, 6), relief='flat', borderwidth=1)
    style.map(key('TButton'), background=[('disabled', c['field']), ('pressed', c['selection']),
                                        ('active', c['hover'])],
              foreground=[('disabled', c['muted'])])
    action_fill = c['accent']
    action_text = c['on_accent']
    style.configure(key('Accent.TButton'), background=action_fill, foreground=action_text,
                    bordercolor=action_fill, lightcolor=action_fill, darkcolor=action_fill,
                    focuscolor=action_text, padding=(10, 6), relief='flat', borderwidth=1)
    style.map(key('Accent.TButton'),
              background=[('disabled', c['field']), ('pressed', _mix(action_fill, c['surface'], .18)),
                          ('active', _mix(action_fill, c['surface'], .1))],
              foreground=[('disabled', c['muted']), ('!disabled', action_text)])
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
    if style.theme_use() != 'clam':
        style.theme_use('clam')
    style.configure('.', background=c['background'], foreground=c['text'],
                    bordercolor=c['border'], troughcolor=c['field'],
                    selectbackground=c['selection'], selectforeground=c['text'], font='TkDefaultFont')
    style.map('.', selectbackground=[('!focus', c['selection'])],
              selectforeground=[('!focus', c['text'])])
    _configure_controls(style, c)
    _configure_controls(style, overlay, 'Overlay.')
    _configure_checkbutton_images(root, style, c, overlay)
    style.configure('Field.TFrame', background=c['field'])
    for family in ('Surface', 'Panel', 'Toolbar'):
        style.configure(f'{family}.TFrame', background=c['surface'])
        style.configure(f'{family}.TLabel', background=c['surface'], foreground=c['text'])
        style.configure(f'{family}.Muted.TLabel', background=c['surface'], foreground=c['muted'])
        for control in ('TCheckbutton', 'TRadiobutton'):
            style.configure(f'{family}.{control}', background=c['surface'], foreground=c['text'],
                            indicatorbackground=c['field'], indicatorforeground=c['accent'],
                            focuscolor=c['accent'], padding=(2, 4))
            style.map(f'{family}.{control}', background=[('active', c['surface'])],
                      indicatorbackground=[('selected', c['accent'])],
                      indicatorforeground=[('selected', c['on_accent'])],
                      foreground=[('disabled', c['muted'])])
    for prefix, backdrop in (('', c['background']), ('Surface.', c['surface']),
                              ('Panel.', c['surface']), ('Toolbar.', c['surface'])):
        style.configure(prefix + 'Switch.TCheckbutton', background=backdrop, foreground=c['text'],
                        focuscolor=c['accent'], padding=(0, 2))
        style.map(prefix + 'Switch.TCheckbutton', background=[('active', backdrop)],
                  foreground=[('disabled', c['muted'])])
        style.configure(prefix + 'Soft.TButton', background=c['selection'], foreground=c['accent'],
                        lightcolor=c['selection'], darkcolor=c['selection'], bordercolor=c['selection'],
                        focuscolor=c['accent'], padding=(12, 5), borderwidth=0, relief='flat')
        style.map(prefix + 'Soft.TButton',
                  background=[('disabled', c['field']), ('pressed', c['sidebar_hover']),
                              ('active', c['hover'])], foreground=[('disabled', c['muted'])])
    for prefix in ('', 'Surface.'):
        name = prefix + 'Segment.TRadiobutton'
        style.configure(name, background=c['field'], foreground=c['muted'],
                        focuscolor=c['accent'], padding=(16, 6), anchor='center', relief='flat', borderwidth=0)
        style.map(name, background=[('disabled', c['field']), ('selected', c['selection']),
                                    ('pressed', c['selection']), ('active', c['hover'])],
                  foreground=[('disabled', c['muted']), ('selected', c['accent']), ('active', c['text'])])
    style.configure('Heading.TLabel', foreground=c['text'], font=('Microsoft YaHei UI', 12, 'bold'))
    style.configure('Surface.Heading.TLabel', background=c['surface'], foreground=c['text'],
                    font=('Microsoft YaHei UI', 12, 'bold'))
    style.configure('Surface.PreviewTitle.TLabel', background=c['surface'], foreground=c['muted'],
                    font=('Microsoft YaHei UI', 11))
    style.configure('Brand.TLabel', background=c['surface'], foreground=c['text'],
                    font=('Microsoft YaHei UI', 12, 'bold'))
    style.configure('PageTitle.TLabel', background=c['background'], foreground=c['text'],
                    font=('Microsoft YaHei UI', 23, 'bold'))
    style.configure('ComposerTitle.TLabel', background=c['surface'], foreground=c['text'],
                    font=('Microsoft YaHei UI', 10, 'bold'))
    style.configure('Sidebar.TFrame', background=c['sidebar'])
    style.configure('Sidebar.TLabel', background=c['sidebar'], foreground=c['sidebar_text'])
    style.configure('Sidebar.Muted.TLabel', background=c['sidebar'], foreground=c['sidebar_muted'])
    style.configure('Sidebar.Brand.TLabel', background=c['sidebar'], foreground=c['sidebar_text'],
                    font=('Microsoft YaHei UI', 14, 'bold'))
    for name, background, foreground in (
            ('Sidebar.TButton', c['sidebar'], c['sidebar_text']),
            ('Sidebar.Selected.TButton', c['sidebar_hover'], c['accent'])):
        style.configure(name, background=background, foreground=foreground,
                        lightcolor=background, darkcolor=background, focuscolor=c['accent'],
                        padding=(12, 10), anchor='w', borderwidth=0, relief='flat',
                        font=('Microsoft YaHei UI', 12))
        style.map(name, background=[('pressed', c['sidebar_hover']), ('active', c['sidebar_hover'])],
                  foreground=[('disabled', c['sidebar_muted']), ('active', foreground)])
    style.configure('Nav.TButton', background=c['background'], foreground=c['muted'],
                    relief='flat', padding=(14, 10), borderwidth=0)
    style.map('Nav.TButton', background=[('pressed', c['selection']), ('active', c['hover'])],
              foreground=[('active', c['text'])])
    style.configure('TLabelframe', background=c['background'], bordercolor=c['border'])
    style.configure('TLabelframe.Label', background=c['background'], foreground=c['muted'])
    style.configure('TNotebook', background=c['background'], borderwidth=0)
    style.configure('Main.TNotebook', background=c['background'], bordercolor=c['background'],
                    lightcolor=c['background'], darkcolor=c['background'])
    style.configure('TNotebook.Tab', background=c['background'], foreground=c['muted'],
                    padding=(16, 9), borderwidth=0)
    style.map('TNotebook.Tab', background=[('selected', c['surface']), ('active', c['hover'])],
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
    _configure_rounded_controls(root, style, c, overlay)
    pending = [(root, 'main')]
    while pending:
        widget, scope = pending.pop()
        scope = getattr(widget, '_hots_theme_scope', scope)
        colors = overlay if scope == 'overlay' else c
        pending.extend((child, scope) for child in widget.winfo_children())
        kind = widget.winfo_class()
        if kind == 'TButton':
            _rounded_alias(style, root._root()._hots_rounded_assets, str(widget.cget('style')))
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
        values = ((20, 0), (33, 2), (35, colorref(colors['surface'])),
                  (36, colorref(colors['text'])))
        supported = False
        for attribute, value in values:
            data = wintypes.DWORD(value)
            supported |= function(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)) == 0
        return supported
    except (OSError, AttributeError):
        return False
