"""Build98348 的聊天/菜单只读字段；不安装钩子、不输入、不读取聊天历史。

CGameUI initializer659150 将 ChatBar/GameMenuDialog 存入+310/+350；
ChatBar initializer6f8260 将 ChatEditBox 存入+170。三个控件已分别完成
关闭、用户手动打开团队空框、用户手动打开ESC菜单的真实字段对照。
调用者负责验证当前 build、进程/窗口、本局身份和 root 生命周期。
"""
from dataclasses import dataclass
import struct

from ..board_sender import ChatInputState


BoardInputState = ChatInputState
ROOT_RVA = 0x338b2e8
ROOT_VTABLE = 0x2653738
CHAT_VTABLE = 0x2659e30
EDIT_VTABLE = 0x27deee0
MENU_VTABLE = 0x265e1b0


@dataclass(frozen=True)
class BoardInputObservation:
    chat_active: bool | None = None
    menu_active: bool | None = None
    channel: str | None = None
    text: str | None = None


def _read(memory, address, size):
    if (type(address) is not int or not 0x10000 <= address
            or address + size >= 0x7fffffff0000 or not 0 < size <= 1024):
        raise ValueError('输入控件指针或读取范围未知')
    data = memory.read(address, size)
    if len(data) != size:
        raise ValueError('输入控件字段读取不完整')
    return data


def _q(memory, address):
    return struct.unpack('<Q', _read(memory, address, 8))[0]


def _visible(memory, frame):
    # CFrame::IsVisible(1), RVA1445ea0；含继承可见位。
    return _read(memory, frame + 0x48, 1)[0] & 0x21 == 0x21


def _objects(memory, base, root):
    if _q(memory, base + ROOT_RVA) != root or _q(memory, root) != base + ROOT_VTABLE:
        raise ValueError('游戏UI根已变化')
    if not _visible(memory, root):
        raise ValueError('游戏UI根当前不可见')
    chat, menu = _q(memory, root + 0x310), _q(memory, root + 0x350)
    if _q(memory, chat) != base + CHAT_VTABLE or _q(memory, menu) != base + MENU_VTABLE:
        raise ValueError('聊天或菜单控件类型未知')
    edit = _q(memory, chat + 0x170)
    if _q(memory, edit) != base + EDIT_VTABLE:
        raise ValueError('聊天编辑控件类型未知')
    return chat, edit, menu


def _draft(memory, edit):
    # Native6f1f4e/6f81f4 直接读取相同UTF8头；不可复用Label显示文本代替原始草稿。
    address = edit + 0x260
    header = _read(memory, address, 16)
    encoded_length, flags, pointer = struct.unpack('<IIQ', header)
    length = encoded_length >> 2
    if not encoded_length or length > 1024:
        raise ValueError('编辑文本缺失或超过上限')
    if flags & 2:
        if not 0x10000 <= pointer < 0x7fffffff0000:
            raise ValueError('编辑文本指针未知')
        raw = _read(memory, pointer, length) if length else b''
    else:
        if length > 7:
            raise ValueError('内联编辑文本长度不正确')
        raw = header[8:8 + length]
    value = raw.decode('utf8')
    if _read(memory, address, 16) != header:
        raise ValueError('编辑文本正在变化')
    return value


class BoardInputReader:
    """独立纯reader；inspect供BoardReader封装统一match_token和进程代次。

    只在可信团队编辑器有效可见时读取其自身草稿，保持原文，不清洗或将缺失转为空串。
    read仅提可见性；snapshot供已持有同局身份的调用者直接生成发送状态。
    """
    @staticmethod
    def _capture(memory, base, root, *, with_text):
        chat, edit, menu = objects = _objects(memory, base, root)
        chat_visible, edit_visible = _visible(memory, chat), _visible(memory, edit)
        chat_active = chat_visible if chat_visible == edit_visible else None
        menu_active = _visible(memory, menu)
        channel = text = None
        if with_text and chat_active is True:
            # SetRecipient6fb0e0 保存audience于+130；静态表2f37220的enum1为ChatRecipientAllies。
            audience = struct.unpack('<I', _read(memory, chat + 0x130, 4))[0]
            if audience == 1:
                channel = 'team'
                try:
                    text = _draft(memory, edit)
                except (OSError, ValueError, UnicodeError, struct.error):
                    pass
                if struct.unpack('<I', _read(memory, chat + 0x130, 4))[0] != audience:
                    raise ValueError('聊天接收方正在变化')
        if _objects(memory, base, root) != objects:
            raise ValueError('输入控件绑定正在变化')
        return BoardInputObservation(chat_active, menu_active, channel, text)

    def _stable(self, memory, base, root, *, with_text):
        try:
            first = self._capture(memory, base, root, with_text=with_text)
            second = self._capture(memory, base, root, with_text=with_text)
            # 草稿或可见性变化只使对应字段未知，独立且一致的观察仍然有效。
            fields = ('chat_active', 'menu_active', 'channel', 'text')
            return BoardInputObservation(*(
                getattr(first, field) if getattr(first, field) == getattr(second, field) else None
                for field in fields))
        except (OSError, ValueError, TypeError, UnicodeError, struct.error):
            return BoardInputObservation()

    def read(self, memory, base, root):
        observation = self._stable(memory, base, root, with_text=False)
        return observation.chat_active, observation.menu_active

    def inspect(self, memory, base, root):
        return self._stable(memory, base, root, with_text=True)

    def snapshot(self, memory, base, root, *, pid, hwnd, match_token):
        observation = self.inspect(memory, base, root)
        if (type(pid) is not int or pid <= 0 or type(hwnd) is not int or hwnd <= 0
                or not isinstance(match_token, str) or not match_token.strip()):
            observation = BoardInputObservation()
        return BoardInputState(pid, hwnd, match_token, observation.chat_active,
                               observation.menu_active, observation.channel, observation.text)
