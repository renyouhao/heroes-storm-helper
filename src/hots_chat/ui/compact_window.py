def position_geometry(x,y):
    # Tk win/tkWinWm.c ParseGeometry: leading '-' means right/bottom relative.
    # An explicit '+' followed by a negative integer preserves absolute coords.
    return f'+{int(x)}+{int(y)}'


def apply_mode(window,mode):
    """同一窗口和 controller 切换布局。"""
    compact=mode=='compact'
    window.root.minsize(540,380 if compact else 420)
    window.root.geometry(('740x520' if compact else '1080x720')+position_geometry(window.settings.x,window.settings.y))
    if compact:window.detail.pack_forget()
    else:window.detail.pack(fill='x',pady=5)
