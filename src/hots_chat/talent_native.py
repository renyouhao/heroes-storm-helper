"""自动天赋Win32组合键边界；导入不加载DLL、不安装hook、不创建线程。"""
import ctypes
import time

from .loot_native import GameTarget, INPUT, Win32LootBackend


CTRL, OEM3 = 0x11, 0xC0
_PHYSICAL_KEYS = (0x10, CTRL, 0x12, 0x5B, 0x5C,
                  0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, OEM3)


class Win32TalentBackend(Win32LootBackend):
    """复用已核验的前台进程身份和INPUT布局；只显式发送一次组合键。"""

    def _physical_key_held(self, *, owned_ctrl=False):
        return any(self.key_down(key) for key in _PHYSICAL_KEYS
                   if not owned_ctrl or key not in (CTRL, 0xA2))

    def _send_keys(self, keys, *, up=False):
        events = (INPUT * len(keys))(*(self._key_input(key, up=up) for key in keys))
        return self.user.SendInput(len(keys), events, ctypes.sizeof(INPUT))

    def _release_keys(self, keys):
        """释放失败时仅补发尚未确认的 keyup，绝不重发 keydown。"""
        try:
            inserted = self._send_keys(keys, up=True)
            if inserted == len(keys):
                return True
            remaining = keys[inserted:] if type(inserted) is int and 0 <= inserted < len(keys) else keys
        except Exception:
            remaining = keys
        try:
            self._send_keys(remaining, up=True)
        except Exception:
            pass
        return False

    def _ready(self, target, allowed, validate, *, owned_ctrl=False):
        if not isinstance(target, GameTarget) or self.foreground_target() != target:
            return False
        if self._physical_key_held(owned_ctrl=owned_ctrl) or (allowed is not None and not allowed()):
            return False
        if validate is not None and not validate():
            return False
        # allowed可能跨线程取消或改变窗口；发送前再次核对，不抢焦点。
        if self.foreground_target() != target or self._physical_key_held(owned_ctrl=owned_ctrl):
            return False
        if allowed is not None and not allowed():
            return False
        if self._foreground_identity() != target or self._physical_key_held(owned_ctrl=owned_ctrl):
            return False
        # 最后的原生查询也可能阻塞；取消请求必须在所有查询之后再次可见。
        if allowed is not None and not allowed():
            return False
        return True

    def select_favorite(self, target, *, allowed=None, validate=None):
        """Ctrl先被游戏观察，再按主键；取消与失败只释放自有按键。

        validate可读游戏；allowed必须是无IO的快速取消门。只有主键尚未
        注入且释放成功时才允许deferred，防止会话误以为可以再次发送。
        """
        if not self._ready(target, allowed, validate):
            return 'deferred'
        held, outcome = [CTRL], 'failed'
        try:
            inserted = self._send_keys((CTRL,))
            if inserted == 0:
                held.clear()
            elif inserted == 1:
                # 同批注入Ctrl与主键时，游戏可能尚未更新修饰键状态。
                time.sleep(.08)
                if not self._ready(target, allowed, validate, owned_ctrl=True):
                    outcome = 'deferred'
                else:
                    held.insert(0, OEM3)
                    inserted = self._send_keys((OEM3,))
                    if inserted == 0:
                        held.remove(OEM3)
                    elif inserted == 1:
                        time.sleep(.08)
                        outcome = 'sent'
        except Exception:
            pass
        finally:
            if held and not self._release_keys(held):
                outcome = 'failed'
        return outcome
