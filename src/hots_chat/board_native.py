"""Tab面板鼠标观察与坐标转换；导入不加载DLL、不安装钩子。"""
import ctypes
import math
import struct
import time
import unicodedata

from .loot_native import BOOL, HANDLE, INPUT, INPUT_MARKER, KEYBDINPUT, LONG, MSLLHOOKSTRUCT, POINT, UINT
from .talent_native import CTRL, Win32TalentBackend


class RECT(ctypes.Structure):
    _fields_ = [('left', LONG), ('top', LONG), ('right', LONG), ('bottom', LONG)]


class Win32BoardBackend(Win32TalentBackend):
    # 回调只复制低级钩子的数字字段。业务排队处理，不能在hook里读进程。
    _mouse_messages = (0x201, 0x202, 0x204, 0x205, 0x207, 0x208,
                       0x20B, 0x20C, 0x20A, 0x20E)

    def __init__(self, *, user=None, kernel=None):
        super().__init__(user=user, kernel=kernel, timer_interval_ms=50)
        self.last_viewport = {}
        signatures = {
            'GetDoubleClickTime': ([], UINT),
            'GetSystemMetrics': ([ctypes.c_int], ctypes.c_int),
            'GetClientRect': ([HANDLE, ctypes.POINTER(RECT)], BOOL),
            'ClientToScreen': ([HANDLE, ctypes.POINTER(POINT)], BOOL),
            'IsIconic': ([HANDLE], BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result

    def _dispatch_mouse(self, on_mouse, message, data):
        event = ctypes.cast(data, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        button = {0x201:'left', 0x202:'left', 0x204:'right', 0x205:'right',
                  0x207:'middle', 0x208:'middle'}.get(message, 'other')
        down = message not in (0x202, 0x205, 0x208, 0x20C)
        on_mouse(int(event.pt.x), int(event.pt.y), down, button,
                 bool(event.flags & 3), int(event.dwExtraInfo))

    def double_click_settings(self):
        return (int(self.user.GetDoubleClickTime()),
                (int(self.user.GetSystemMetrics(36)), int(self.user.GetSystemMetrics(37))))

    def _physical_key_held(self, *, owned_ctrl=False):
        ignored = (CTRL, 0xA2) if owned_ctrl else ()
        # 每次发送前检查；hook回调不做这组查询。鼠标还按着也不开始聊天。
        return any(self.key_down(key) for key in range(1, 255) if key not in ignored)

    def input_idle(self):
        return not self._physical_key_held()

    def open_team_chat(self, target, *, allowed=lambda:True):
        if not self._ready(target, allowed, None):return False
        held, success = [CTRL], False
        try:
            inserted = self._send_keys((CTRL,))
            if inserted == 0:held.clear()
            if inserted != 1:return False
            time.sleep(.08)
            if not self._ready(target, allowed, None, owned_ctrl=True):return False
            held.insert(0, 0x0D)
            inserted = self._send_keys((0x0D,))
            if inserted == 0:held.remove(0x0D)
            if inserted != 1:return False
            time.sleep(.05)
            success = True
        except OSError:
            pass
        finally:
            if held and not self._release_keys(held):success = False
        return success

    @staticmethod
    def _unicode_input(unit, *, up=False):
        event = INPUT()
        event.type = 1
        event.ki = KEYBDINPUT(0, unit, 6 if up else 4, 0, INPUT_MARKER)
        return event

    def type_unicode(self, text, target, *, allowed=lambda:True):
        if (not isinstance(text, str) or not 0 < len(text) <= 240
                or any(unicodedata.category(c).startswith('C') for c in text)):
            return False
        raw = text.encode('utf-16-le', errors='strict')
        units = struct.unpack('<' + 'H' * (len(raw)//2), raw)
        for start in range(0, len(units), 16):
            if not self._ready(target, allowed, None):return False
            chunk = units[start:start+16]
            events = (INPUT * (len(chunk)*2))(*(
                self._unicode_input(unit, up=up) for unit in chunk for up in (False, True)))
            try:
                if self.user.SendInput(len(events), events, ctypes.sizeof(INPUT)) == len(events):continue
            except OSError:
                pass
            # 插入数不明时只补自有Unicode keyup，不重发正文或Enter。
            releases = (INPUT * len(chunk))(*(self._unicode_input(unit, up=True) for unit in chunk))
            try:self.user.SendInput(len(releases), releases, ctypes.sizeof(INPUT))
            except OSError:pass
            return False
        return True

    def submit_chat(self, target, *, allowed=lambda:True):
        if not self._ready(target, allowed, None):return False
        success, held = False, [0x0D]
        try:
            inserted = self._send_keys((0x0D,))
            if inserted == 0:held.clear()
            if inserted != 1:return False
            time.sleep(.05)
            success = True
        except OSError:
            pass
        finally:
            if held and not self._release_keys(held):success = False
        return success

    def inspect_viewport(self, hwnd):
        """只读窗口几何，不保存标题、进程路径或窗口句柄。"""
        info = dict(coordinate_reason='invalid_window')
        if type(hwnd) is not int or hwnd <= 0:
            return info
        if self.user.IsIconic(hwnd):
            return dict(coordinate_reason='minimized')
        client, origin = RECT(), POINT()
        if not self.user.GetClientRect(hwnd, ctypes.byref(client)):
            return dict(coordinate_reason='client_rect_failed')
        info.update(client_width=client.right-client.left, client_height=client.bottom-client.top)
        if not self.user.ClientToScreen(hwnd, ctypes.byref(origin)):
            info['coordinate_reason'] = 'client_origin_failed'
            return info
        info.update(origin_x=origin.x, origin_y=origin.y, coordinate_reason='ok')
        return info

    def viewport_converter(self, hwnd, rect, render_size):
        """把引擎render像素矩形转桌面物理坐标；不拿截图缩放比作依据。

        仅接受等比例完整客户区；黑边或变更中的窗口保守返回unknown。
        运行入口已启用PerMonitorV2，客户区与低级鼠标hook使用同一物理坐标系。
        """
        self.last_viewport = dict(coordinate_reason='invalid_geometry')
        if (type(hwnd) is not int or hwnd <= 0 or not isinstance(rect, (tuple, list))
                or not isinstance(render_size, (tuple, list)) or len(rect) != 4 or len(render_size) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in (*rect, *render_size))):
            return None
        left, top, right, bottom = rect
        rw, rh = render_size
        self.last_viewport.update(render_width=rw, render_height=rh)
        if not (0 < rw <= 32768 and 0 < rh <= 32768
                and 0 <= left < right <= rw and 0 <= top < bottom <= rh):
            return None
        self.last_viewport.update(self.inspect_viewport(hwnd))
        if self.last_viewport['coordinate_reason'] != 'ok':
            return None
        width, height = self.last_viewport['client_width'], self.last_viewport['client_height']
        if not (0 < width <= 32768 and 0 < height <= 32768
                and abs(width / height / (rw / rh) - 1) <= .01):
            self.last_viewport['coordinate_reason'] = 'aspect_mismatch_or_client_size'
            if 0 < width <= 32768 and 0 < height <= 32768:
                self.last_viewport['coordinate_reason'] = 'aspect_mismatch'
            return None
        x, y = self.last_viewport['origin_x'], self.last_viewport['origin_y']
        result = (round(x + left * width / rw), round(y + top * height / rh),
                  round(x + right * width / rw), round(y + bottom * height / rh))
        if result[0] >= result[2] or result[1] >= result[3]:
            self.last_viewport['coordinate_reason'] = 'rounded_empty_rect'
        return result if result[0] < result[2] and result[1] < result[3] else None
