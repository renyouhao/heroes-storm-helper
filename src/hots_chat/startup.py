"""启动前保护：仅依赖标准库，必须先于应用及 CLR 导入。"""
from datetime import datetime
import ctypes
import os
from pathlib import Path
import stat
import sys
import traceback


STARTUP_LOG_LIMIT = 128 * 1024
# 与锁定的 pythonnet / WebView2 加载路径对应，不扫描或解锁其它文件。
BUNDLED_MANAGED_DLLS = (
    'pythonnet/runtime/Python.Runtime.dll',
    'webview/lib/Microsoft.Web.WebView2.Core.dll',
    'webview/lib/Microsoft.Web.WebView2.WinForms.dll',
)
UNBLOCK_GUIDANCE = (
    '如果使用已核验的官方压缩包，请右键原 ZIP→属性→解除锁定，再重新解压完整文件夹（含 _internal）。'
    '已经解压时需对完整应用目录解除锁定，只解除 EXE 的锁定不够。'
)


class StartupPreparationError(RuntimeError):
    """当前发行包的内置运行库无法安全准备。"""


def _bundled_file(root, relative):
    """白名单文件及其包内父目录均不得通过重解析点跳出发行包。"""
    candidate = root / relative
    resolved_root = root.resolve()
    if not candidate.resolve().is_relative_to(resolved_root):
        raise StartupPreparationError('内置运行库路径超出应用目录。')
    for item in (candidate, *candidate.parents):
        if item.is_symlink() or (item.exists() and
                getattr(item.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise StartupPreparationError('内置运行库路径包含符号链接或目录联接。')
        if item == root:
            break
    return candidate


def prepare_bundled_runtime():
    """仅处理冻结 Windows 包的已知托管 DLL 的下载标记，保留二进制内容。"""
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return ()
    bundle = getattr(sys, '_MEIPASS', None)
    if not bundle:
        return ()
    root = Path(bundle).absolute()
    prepared = []
    for relative in BUNDLED_MANAGED_DLLS:
        candidate = _bundled_file(root, relative)
        if not candidate.is_file():
            continue
        try:
            Path(str(candidate) + ':Zone.Identifier').unlink()
        except FileNotFoundError:
            continue
        except OSError as failure:
            raise StartupPreparationError('无法解除内置运行库的下载锁定：' + relative) from failure
        prepared.append(relative)
    return tuple(prepared)


def _write_startup_log(error):
    base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'HotsChatHelper'
    path = base / 'startup.log'
    # 只保留最近一次失败；不读取凭据、环境快照、命令参数或栈帧局部变量。
    detail = ''.join(traceback.TracebackException.from_exception(error, capture_locals=False).format(chain=True))
    payload = (datetime.now().astimezone().isoformat() + '\n' + detail).encode('utf-8', errors='replace')
    if len(payload) > STARTUP_LOG_LIMIT:
        marker = b'[earlier traceback truncated]\n'
        tail = payload[-(STARTUP_LOG_LIMIT - len(marker)):].decode('utf-8', errors='ignore').encode('utf-8')
        payload = marker + tail
    base.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _show_startup_message(message):
    if sys.platform == 'win32':
        # 独立于 Tk、.NET 和 WebView2，窗口创建失败时仍能给出提示。
        ctypes.windll.user32.MessageBoxW(None, message, '风暴英雄助手 · 启动失败', 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def report_startup_failure(error, *, message='助手启动失败，请根据启动日志检查运行环境或重新解压完整应用目录。'):
    """日志写入失败也应保留可见错误；弹窗不直接暴露底层异常正文。"""
    try:
        path = _write_startup_log(error)
        log_message = '启动日志：' + str(path)
    except Exception:
        path = None
        log_message = '启动日志未能写入，请检查当前用户目录的写入权限。'
    text = message + '\n\n错误类别：' + type(error).__name__ + '\n' + log_message + '\n\n' + UNBLOCK_GUIDANCE
    try:
        _show_startup_message(text)
    except Exception:
        if sys.stderr is not None:
            try:
                print(text, file=sys.stderr)
            except Exception:
                pass
    return path


def run(main_factory=None):
    """保护应用首次导入和运行；正常 SystemExit 及冻结子进程由入口自行处理。"""
    try:
        prepare_bundled_runtime()
        if main_factory is None:
            from .app import main
        else:
            main = main_factory()
        return main()
    except Exception as failure:
        message = ('无法准备应用内置运行库，请检查完整解压目录及文件权限。'
                   if isinstance(failure, StartupPreparationError) else
                   '助手启动失败，请根据启动日志检查运行环境或重新解压完整应用目录。')
        report_startup_failure(failure, message=message)
        return 2
