"""自动天赋Win32组合键边界；导入不加载DLL、不安装hook、不创建线程。"""
import ctypes

from .loot_native import GameTarget, INPUT, Win32LootBackend


CTRL, OEM3 = 0x11, 0xC0
_PHYSICAL_KEYS = (0x10, CTRL, 0x12, 0x5B, 0x5C,
                  0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, OEM3)


class Win32TalentBackend(Win32LootBackend):
    """复用已核验的前台进程身份和INPUT布局；只显式发送一次组合键。"""

    def _physical_key_held(self):
        return any(self.key_down(key) for key in _PHYSICAL_KEYS)

    def _release_partial(self, inserted):
        # SendInput按数组顺序插入。只清理尚未收到keyup的自有keydown。
        keys = {0: (), 1: (CTRL,), 2: (OEM3, CTRL), 3: (CTRL,)}.get(
            inserted, (OEM3, CTRL))
        if not keys:
            return
        releases = (INPUT * len(keys))(*(self._key_input(key, up=True) for key in keys))
        try:
            self.user.SendInput(len(keys), releases, ctypes.sizeof(INPUT))
        except Exception:
            pass

    def select_favorite(self, target, *, allowed=None, validate=None):
        """validate可读游戏；allowed必须是无IO的快速取消门。

        慢核验完成后再检查前台和物理键；最后仅查询快速取消门。
        返回sent/deferred/failed；部分失败只释放，不重试keydown。
        """
        if not isinstance(target, GameTarget) or self.foreground_target() != target:
            return 'deferred'
        if self._physical_key_held() or (allowed is not None and not allowed()):
            return 'deferred'
        if validate is not None and not validate():
            return 'deferred'
        # allowed可能跨线程取消或改变窗口；发送前再次核对，不抢焦点。
        if self.foreground_target() != target or self._physical_key_held():
            return 'deferred'
        if allowed is not None and not allowed():
            return 'deferred'
        if self._foreground_identity() != target or self._physical_key_held():
            return 'deferred'
        # 最后的原生查询也可能阻塞；取消请求必须在所有查询之后再次可见。
        if allowed is not None and not allowed():
            return 'deferred'
        chord = (INPUT * 4)(self._key_input(CTRL), self._key_input(OEM3),
                            self._key_input(OEM3, up=True), self._key_input(CTRL, up=True))
        inserted = None
        try:
            inserted = self.user.SendInput(4, chord, ctypes.sizeof(INPUT))
            if inserted == 4:
                return 'sent'
        except Exception:
            pass
        self._release_partial(inserted)
        return 'failed'
