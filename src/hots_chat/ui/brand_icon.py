"""Canonical purple assistant mark for all application windows."""
from pathlib import Path
import sys


def assistant_icon_path():
    resource_root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[3]))
    return resource_root / 'data' / 'assistant.ico'


def apply_assistant_icon(window, *, default=False):
    """Apply the mark and optionally make owned Tk windows inherit it."""
    import tkinter as tk
    try:
        path=str(assistant_icon_path())
        window.iconbitmap(path)
        if default:window.iconbitmap(default=path)
    except (tk.TclError, OSError):
        return False
    return True
