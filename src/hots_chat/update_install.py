"""独立助手升级进程：先等待原进程退出，再整体替换并保留回滚备份。"""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from . import APP_NAME, __version__
from .updater import MAX_FILES, MAX_UNPACKED_BYTES, StagedUpdate, UpdateError, Version, validate_bundle

_ENTRY = APP_NAME + '.exe'
_TIMEOUT = 120


@dataclass(frozen=True)
class InstallPlan:
    path: Path
    workspace: Path
    target_dir: Path
    helper_executable: Path
    candidate_dir: Path
    backup_dir: Path
    failed_dir: Path
    token: str

    @property
    def ready_path(self):
        return self.workspace / 'helper.ready'


def _is_link(path):
    metadata = path.lstat()
    return path.is_symlink() or bool(getattr(metadata, 'st_file_attributes', 0) & 0x400)


def _plain_path(path):
    path = Path(path)
    if not path.is_absolute():
        raise UpdateError('升级路径必须是绝对路径。')
    for ancestor in (path, *path.parents):
        if ancestor.exists() and _is_link(ancestor):
            raise UpdateError('升级路径不能包含符号链接或目录联接。')
    return path.resolve()


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(256 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_manifest(root):
    root = _plain_path(root)
    if not root.is_dir():
        raise UpdateError('升级目录不存在。')
    result, total = {}, 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if _is_link(path):
                raise UpdateError('升级目录包含链接或目录联接。')
            if path.is_dir():
                continue
            if not path.is_file():
                raise UpdateError('升级目录包含特殊文件。')
            total += path.stat().st_size
            if total > MAX_UNPACKED_BYTES or len(result) >= MAX_FILES:
                raise UpdateError('升级目录大小或文件数量超出限制。')
            result[path.relative_to(root).as_posix()] = _digest(path)
    return result


def _validate_target(target, executable):
    target, executable = _plain_path(target), _plain_path(executable)
    if target.parent == target or executable.parent != target or executable.name != _ENTRY:
        raise UpdateError('只允许替换当前风暴英雄助手的完整发行目录。')
    for variable in ('LOCALAPPDATA', 'APPDATA', 'WINDIR', 'SystemRoot'):
        value = os.environ.get(variable)
        if value:
            protected = Path(value).resolve()
            if target.is_relative_to(protected) or protected.is_relative_to(target):
                raise UpdateError('升级不能替换 AppData 或系统目录。')
    if (target / '.git').exists() or (target / 'HeroesOfTheStorm_x64.exe').exists():
        raise UpdateError('升级不能替换源码工作区或游戏目录。')
    validate_bundle(target)
    return target


def _kernel():
    if sys.platform != 'win32':
        raise UpdateError('自动替换升级仅支持 Windows 发行版。')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                      ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                      ctypes.POINTER(wintypes.FILETIME)]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    return kernel


def _creation_token(kernel, handle):
    creation, exited, system, user = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exited),
                                  ctypes.byref(system), ctypes.byref(user)):
        raise UpdateError('无法确认原助手进程身份。')
    return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)


def _process_token(pid):
    kernel = _kernel()
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise UpdateError('无法读取原助手进程身份，已停止安装。')
    try:
        return _creation_token(kernel, handle)
    finally:
        kernel.CloseHandle(handle)


def _wait_for_exit(pid, token, timeout, *, on_ready=None):
    kernel = _kernel()
    handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # Process has already exited.
            if on_ready:
                on_ready()
            return
        raise UpdateError('无法等待原助手退出，已保留原安装。')
    try:
        # A reused PID belongs to a later process; never kill or wait for that process.
        if _creation_token(kernel, handle) != token:
            if on_ready:
                on_ready()
            return
        # Readiness means the original process handle is held and its identity
        # has been checked, so a startup failure cannot strand the parent.
        if on_ready:
            on_ready()
        if kernel.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
            raise UpdateError('等待原助手退出超时，已保留原安装。')
    finally:
        kernel.CloseHandle(handle)


def _write_json(path, payload):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def prepare_install(staged, *, install_dir=None, pid=None, frozen=None, executable=None, process_token=None,
                    progress=None):
    progress = progress or (lambda detail: None)
    if not (getattr(sys, 'frozen', False) if frozen is None else frozen):
        raise UpdateError('源码运行模式不支持原地安装，请下载发行包后手动使用。')
    if not isinstance(staged, StagedUpdate) or staged.release.asset is None:
        raise UpdateError('升级暂存信息无效。')
    if Version.parse(staged.release.version) <= Version.parse(__version__):
        raise UpdateError('升级版本必须高于当前助手版本。')
    executable = _plain_path(executable or sys.executable)
    target = _validate_target(install_dir or executable.parent, executable)
    workspace = _plain_path(staged.workspace)
    if (not workspace.name.startswith('hots-update-') or workspace.is_relative_to(target)
            or target.is_relative_to(workspace)):
        raise UpdateError('升级暂存目录必须位于现有安装目录之外。')
    bundle, archive = _plain_path(staged.bundle_dir), _plain_path(staged.archive_path)
    if bundle != workspace / 'unpacked' / APP_NAME or archive != workspace / 'package.zip':
        raise UpdateError('升级暂存目录结构无效。')
    progress('正在校验升级压缩包…')
    if _digest(archive) != staged.release.asset.sha256:
        raise UpdateError('升级包 SHA256 已改变，已停止安装。')
    validate_bundle(bundle)
    progress('正在校验新版文件…')
    if not staged.files or _tree_manifest(bundle) != staged.files:
        raise UpdateError('暂存升级文件已改变，请重新下载。')
    progress('正在校验当前安装文件…')
    original_files = _tree_manifest(target)
    if any(Path(name).name.casefold() in ('heroesofthestorm_x64.exe', 'heroesofthestorm.exe') for name in original_files):
        raise UpdateError('不能将游戏目录作为助手升级目标。')
    pid = os.getpid() if pid is None else pid
    if type(pid) is not int or pid <= 0 or pid == 4:
        raise UpdateError('原助手进程标识无效。')
    process_token = _process_token(pid) if process_token is None else process_token
    if not isinstance(process_token, str) or not process_token or len(process_token) > 128:
        raise UpdateError('原助手进程身份令牌无效。')
    token = uuid.uuid4().hex
    candidate = target.parent / f'.{APP_NAME}.update-{token}'
    backup = target.parent / f'.{APP_NAME}.backup-{token}'
    failed = target.parent / f'.{APP_NAME}.failed-{token}'
    helper = workspace / 'helper'
    plan = InstallPlan(workspace / 'install-plan.json', workspace, target, helper / _ENTRY,
                       candidate, backup, failed, token)
    if plan.path.exists() or helper.exists():
        raise UpdateError('此升级已准备过，请重新下载后重试。')
    helper_created = candidate_created = False
    try:
        # Both copies are inert. The candidate shares the target volume for directory renames.
        helper.mkdir()
        helper_created = True
        progress('正在复制独立升级程序及运行库…')
        shutil.copytree(target, helper, dirs_exist_ok=True)
        progress('正在校验独立升级程序副本…')
        if _tree_manifest(helper) != original_files:
            raise UpdateError('独立升级程序副本校验失败。')
        candidate.mkdir()
        candidate_created = True
        progress('正在准备新版安装目录…')
        shutil.copytree(bundle, candidate, dirs_exist_ok=True)
        progress('正在校验新版安装目录…')
        if _tree_manifest(candidate) != staged.files:
            raise UpdateError('升级候选目录校验失败。')
        identity = target.stat()
        payload = {'schema': 1, 'token': token, 'workspace': str(workspace), 'target_dir': str(target),
                   'helper_executable': str(plan.helper_executable), 'candidate_dir': str(candidate),
                   'backup_dir': str(backup), 'failed_dir': str(failed), 'pid': pid,
                   'process_token': process_token, 'target_identity': [identity.st_dev, identity.st_ino],
                   'original_entry_sha256': original_files[_ENTRY], 'files': staged.files,
                   'helper_files': original_files}
        _write_json(plan.path, payload)
        return plan
    except (OSError, UpdateError) as failure:
        for owned, created in ((helper, helper_created), (candidate, candidate_created)):
            if created and owned.is_dir() and not _is_link(owned):
                shutil.rmtree(owned)
        if isinstance(failure, UpdateError):
            raise
        raise UpdateError('无法准备升级，请检查安装目录写入权限和可用磁盘空间。') from failure


def _read_plan(path):
    path = _plain_path(path)
    if path.name != 'install-plan.json' or not path.parent.name.startswith('hots-update-'):
        raise UpdateError('升级计划路径无效。')
    if path.stat().st_size > 4 * 1024 * 1024:
        raise UpdateError('升级计划大小超出限制。')
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict) or payload.get('schema') != 1:
        raise UpdateError('升级计划格式无效。')
    token = payload.get('token')
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token):
        raise UpdateError('升级计划令牌无效。')
    workspace = path.parent
    target = _plain_path(payload['target_dir'])
    expected = {'workspace': workspace, 'helper_executable': workspace / 'helper' / _ENTRY,
                'candidate_dir': target.parent / f'.{APP_NAME}.update-{token}',
                'backup_dir': target.parent / f'.{APP_NAME}.backup-{token}',
                'failed_dir': target.parent / f'.{APP_NAME}.failed-{token}'}
    for name, value in expected.items():
        if _plain_path(payload[name]) != value:
            raise UpdateError('升级计划目录边界无效。')
    if workspace.is_relative_to(target) or target.is_relative_to(workspace):
        raise UpdateError('独立升级程序不能位于待替换目录中。')
    if type(payload.get('pid')) is not int or payload['pid'] <= 0 or payload['pid'] == 4:
        raise UpdateError('升级计划的进程标识无效。')
    if not isinstance(payload.get('process_token'), str) or not payload['process_token']:
        raise UpdateError('升级计划的进程令牌无效。')
    for manifest in ('files', 'helper_files'):
        files = payload.get(manifest)
        if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
            raise UpdateError('升级计划校验清单无效。')
        for name, digest in files.items():
            if (not isinstance(name, str) or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)
                    or '\\' in name or ':' in name or name.startswith('/') or '..' in name.split('/')):
                raise UpdateError('升级计划文件校验项无效。')
    plan = InstallPlan(path, workspace, target, expected['helper_executable'], expected['candidate_dir'],
                       expected['backup_dir'], expected['failed_dir'], token)
    return plan, payload


def _validate_original(plan, payload):
    _validate_target(plan.target_dir, plan.target_dir / _ENTRY)
    identity = plan.target_dir.stat()
    if ([identity.st_dev, identity.st_ino] != payload.get('target_identity')
            or _digest(plan.target_dir / _ENTRY) != payload.get('original_entry_sha256')):
        raise UpdateError('原助手安装目录已改变，已停止替换。')


def _ready_wait(plan, process):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise UpdateError('独立升级程序未能启动，已保留当前助手。')
        result = plan.workspace / 'result.json'
        if result.exists():
            raise UpdateError('独立升级程序已结束或失败，已保留当前助手。')
        if plan.ready_path.is_file() and plan.ready_path.read_text(encoding='ascii') == plan.token:
            if process.poll() is not None or result.exists():
                raise UpdateError('独立升级程序已停止，已保留当前助手。')
            return
        time.sleep(0.05)
    # The original assistant remains alive, so the helper cannot replace it; cancel its wait.
    (plan.workspace / 'cancel.request').write_text(plan.token, encoding='ascii')
    raise UpdateError('独立升级程序启动超时，已保留当前助手。')


def launch_install(plan, *, popen=None, wait_ready=None):
    loaded, payload = _read_plan(plan.path)
    _validate_original(loaded, payload)
    if _tree_manifest(loaded.helper_executable.parent) != payload['helper_files']:
        raise UpdateError('独立升级程序副本已改变，已停止安装。')
    if loaded.ready_path.exists():
        raise UpdateError('升级程序已经启动，请勿重复安装。')
    command = [str(loaded.helper_executable), '--apply-update', str(loaded.path)]
    try:
        process = (popen or subprocess.Popen)(command, cwd=str(loaded.helper_executable.parent),
                                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                              close_fds=True)
        (wait_ready or _ready_wait)(loaded, process)
        return process
    except OSError as failure:
        raise UpdateError('无法启动独立升级程序，已保留当前助手。') from failure


def _restart(executable):
    return subprocess.Popen([str(executable)], cwd=str(executable.parent),
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)


def _start_and_confirm(restart, executable):
    process = restart(executable)
    if process is None:
        # Injected boundaries may perform their own validated restart. The real
        # launcher always returns Popen and is always probed here.
        return
    if process.poll() is not None:
        raise UpdateError('新版助手启动后立即退出，正在恢复原安装。')
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        return
    raise UpdateError('助手未通过启动存活检查，正在恢复原安装。')


def apply_update(plan_path, *, wait_for_exit=None, restart=None, rename=None, helper_executable=None):
    """仅由 --apply-update 入口调用；返回 0 成功、2 失败并在暂存目录留结果。"""
    plan = None
    old_moved = new_moved = parent_exited = False
    rename = rename or os.replace
    restart = restart or _restart
    try:
        plan, payload = _read_plan(plan_path)
        if _plain_path(helper_executable or sys.executable) != plan.helper_executable:
            raise UpdateError('升级必须由安装目录外的独立助手副本执行。')
        try:
            with (plan.workspace / 'helper.lock').open('x', encoding='ascii') as lock:
                lock.write(plan.token)
        except FileExistsError as failure:
            raise UpdateError('此升级计划已有独立进程处理，已停止重复执行。') from failure
        _validate_original(plan, payload)
        if plan.backup_dir.exists() or plan.failed_dir.exists() or (plan.workspace / 'result.json').exists():
            raise UpdateError('此升级计划已经执行过，已拒绝重复替换。')
        validate_bundle(plan.candidate_dir)
        if _tree_manifest(plan.candidate_dir) != payload['files']:
            raise UpdateError('升级候选文件已改变，已保留原安装。')
        def ready():
            plan.ready_path.write_text(plan.token, encoding='ascii')

        if wait_for_exit is None:
            _wait_for_exit(payload['pid'], payload['process_token'], _TIMEOUT, on_ready=ready)
        else:
            # A supplied waiter owns the process boundary and represents a
            # successfully prepared wait, allowing wholly inert offline tests.
            ready()
            wait_for_exit(payload['pid'], payload['process_token'], _TIMEOUT)
        if (plan.workspace / 'cancel.request').exists():
            raise UpdateError('升级已取消，已保留原安装。')
        parent_exited = True
        # Recheck after waiting: the original process may have remained open for two minutes.
        _validate_original(plan, payload)
        if _tree_manifest(plan.candidate_dir) != payload['files']:
            raise UpdateError('等待期间升级文件已改变，已保留原安装。')
        rename(plan.target_dir, plan.backup_dir)
        old_moved = True
        rename(plan.candidate_dir, plan.target_dir)
        new_moved = True
        _start_and_confirm(restart, plan.target_dir / _ENTRY)
        try:
            _write_json(plan.workspace / 'result.json', {'status': 'installed', 'backup_dir': str(plan.backup_dir)})
        except OSError:
            # A report failure must not move a newly running application underneath itself.
            pass
        return 0
    except Exception as failure:
        rollback_error = None
        if plan is not None and old_moved:
            try:
                if new_moved:
                    rename(plan.target_dir, plan.failed_dir)
                rename(plan.backup_dir, plan.target_dir)
                _start_and_confirm(restart, plan.target_dir / _ENTRY)
            except Exception:
                rollback_error = '自动恢复或重启未完成，请从备份目录恢复助手。'
        elif plan is not None and parent_exited:
            try:
                _validate_original(plan, payload)
                _start_and_confirm(restart, plan.target_dir / _ENTRY)
            except Exception:
                rollback_error = '原安装已保留，请手动重新启动助手。'
        if plan is not None:
            message = str(failure) if isinstance(failure, UpdateError) else '升级替换失败。'
            try:
                _write_json(plan.workspace / 'result.json', {'status': 'failed', 'message': message,
                            'rollback_error': rollback_error, 'backup_dir': str(plan.backup_dir)})
            except OSError:
                pass
        return 2
