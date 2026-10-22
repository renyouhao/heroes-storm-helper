"""安全的未捕获异常钩子；不格式化异常原文或局部变量。"""
from pathlib import Path
import sys
import threading


class DiagnosticHooks:
    def __init__(self, recorder, root=None):
        self.recorder, self.root = recorder, root
        self._sys, self._thread = sys.excepthook, threading.excepthook
        self._tk = None
        self._sys_callback = lambda *args: self._report('main', self._sys, *args)
        self._thread_callback = self._thread_error
        self._tk_callback = lambda *args: self._report('tk', self._tk, *args)
        sys.excepthook, threading.excepthook = self._sys_callback, self._thread_callback
        if root is not None:
            self.attach_root(root)

    def attach_root(self, root):
        self.root = root
        self._tk = getattr(root,'report_callback_exception',None)
        if callable(self._tk):root.report_callback_exception = self._tk_callback

    def capture(self, stage, error):
        self._report(stage, None, type(error), error, error.__traceback__)

    def _report(self, stage, previous, error_type, error, traceback):
        frames = []
        current = traceback
        while current is not None and len(frames) < 8:
            filename = Path(current.tb_frame.f_code.co_filename)
            if 'hots_chat' in filename.parts:
                frames.append(f'{filename.name}:{current.tb_lineno}')
            current = current.tb_next
        self.recorder.record('runtime.exception', stage=stage, error_type=error_type.__name__,
                             frames=';'.join(frames), winerror=getattr(error, 'winerror', None))
        if previous is not None:
            previous(error_type, error, traceback)

    def _thread_error(self, args):
        self._report('thread', None, args.exc_type, args.exc_value, args.exc_traceback)
        self._thread(args)

    def close(self):
        if sys.excepthook is self._sys_callback:
            sys.excepthook = self._sys
        if threading.excepthook is self._thread_callback:
            threading.excepthook = self._thread
        if self.root is not None and getattr(self.root,'report_callback_exception',None) is self._tk_callback:
            self.root.report_callback_exception = self._tk
