"""用Tk自身文字布局限制物理行数，并绘制细暗边字幕。"""


def fit_canvas_text(canvas, item, text, *, font, max_lines):
    """检查真实Canvas文字的bbox，混排、CJK与单个长词使用相同规则。"""
    text = ' '.join(str(text).split())
    max_height = max(1, font.metrics('linespace') * max_lines)

    def fits(value):
        canvas.itemconfigure(item, text=value)
        bounds = canvas.bbox(item)
        return bounds is None or bounds[3] - bounds[1] <= max_height

    if fits(text):
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if fits(text[:middle] + '…'):
            low = middle
        else:
            high = middle - 1
    result = text[:low] + '…'
    canvas.itemconfigure(item, text=result)
    return result


def draw_outlined_text(canvas, text, *, x, y, width, font, max_lines, edge=1):
    """前景真实参与测量；偏移一圈的文字在它下方组成细边。"""
    item = canvas.create_text(x, y, text='', width=width, font=font,
                              anchor='n', justify='center', fill='#ffffff',
                              tags=('foreground',))
    fitted = fit_canvas_text(canvas, item, text, font=font, max_lines=max_lines)
    for dx, dy in ((-edge, -edge), (0, -edge), (edge, -edge),
                   (-edge, 0), (edge, 0),
                   (-edge, edge), (0, edge), (edge, edge)):
        canvas.create_text(x + dx, y + dy, text=fitted, width=width, font=font,
                           anchor='n', justify='center', fill='#141414',
                           tags=('outline',))
    canvas.tag_raise(item)
    return canvas.bbox(item)
