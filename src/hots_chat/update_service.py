"""升级任务与Tk隔离；正常启动可检查一次，下载和安装仍需显式操作。"""
from dataclasses import dataclass, replace
from collections import deque
import threading

from . import __version__


@dataclass(frozen=True)
class UpdateStatus:
    state: str = 'idle'
    detail: str = '点击检查更新，查看最新正式版本。'
    release: object = None
    staged: object = None
    downloaded: int = 0
    total: int = 0
    generation: int = 0


class UpdateService:
    def __init__(self, *, version=__version__, check=None, download=None, staging_root=None, discard=None):
        self.version = version
        self._check = check or self._default_check
        self._download = download or self._default_download
        self._discard = discard or self._default_discard
        self.staging_root = staging_root
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._closed = False
        self._generation = 0
        self._status = UpdateStatus()
        self._startup_check_attempted = False
        self._available_release = None
        self.thread = None
        self._owned_stage = None
        self._cleanup_queue = deque()
        self._cleanup_running = False
        self.cleanup_thread = None

    @staticmethod
    def _default_check(version):
        from .updater import check_for_update
        return check_for_update(version)

    @staticmethod
    def _default_download(release, **kwargs):
        from .updater import download_update
        return download_update(release, **kwargs)

    @staticmethod
    def _default_discard(staged, **kwargs):
        from .updater import discard_update
        return discard_update(staged, **kwargs)

    def _queue_cleanup(self, staged):
        if staged is None:return
        # This method runs under _lock, and performs no filesystem work on Tk.
        if len(self._cleanup_queue) >= 16:return
        self._cleanup_queue.append(staged)
        if not self._cleanup_running:
            self._cleanup_running = True
            self.cleanup_thread = threading.Thread(target=self._cleanup, name='helper-update-cleanup', daemon=True)
            self.cleanup_thread.start()

    def _cleanup(self):
        while True:
            with self._lock:
                if not self._cleanup_queue:
                    self._cleanup_running = False
                    return
                staged = self._cleanup_queue.popleft()
            try:self._discard(staged, staging_root=self.staging_root)
            except Exception:pass  # Cleanup is best effort; an unprovable directory is retained.

    def _release_owned_stage(self):
        staged, self._owned_stage = self._owned_stage, None
        self._queue_cleanup(staged)

    @property
    def latest(self):
        with self._lock:return self._status

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    @property
    def has_available_update(self):
        # 检查、下载失败或取消任务不否定此前成功确认的新版。
        with self._lock:return self._available_release is not None

    def _publish(self, generation, **changes):
        with self._lock:
            if not self._closed and generation == self._generation and not self._cancel.is_set():
                self._status = replace(self._status, generation=generation, **changes)
                return True
            return False

    def check(self):
        return self._start('checking', self._run_check)

    def check_on_startup(self):
        """每个服务实例只尝试一次；用户已开始过更新操作时复用其结果。"""
        with self._lock:
            if self._startup_check_attempted or self._closed:return False
            self._startup_check_attempted = True
            if self._status.state != 'idle':return False
            return self.check()

    def download(self):
        with self._lock:
            if self._status.state != 'available' or self._status.release is None:return False
            release = self._status.release
            return self._start('downloading', lambda gen, cancel:self._run_download(gen, cancel, release))

    def install(self, prepare_and_launch):
        with self._lock:
            if self._status.state != 'downloaded' or self._status.staged is None:return False
            staged = self._status.staged
            def operation(generation, cancelled):
                with self._lock:
                    if self._closed or cancelled.is_set():
                        self._release_owned_stage()
                        return
                    # Transfer ownership before the first preparation side effect.
                    # A live helper must keep its executable and plan after close.
                    self._owned_stage = None
                prepare_and_launch(staged)
                # helper已就绪后不能当成可撤销下载；主窗下一次pump关闭整个助手。
                self._publish(generation, state='restarting', detail='升级程序已就绪，正在退出助手…')
            return self._start('preparing', operation)

    def report_install_progress(self, detail):
        with self._lock:
            if self._status.state == 'preparing':
                return self._publish(self._generation, detail=detail)
            return False

    def _start(self, state, operation):
        with self._lock:
            if self._closed or self.busy:return False
            if state != 'preparing':self._release_owned_stage()
            self._generation += 1
            generation = self._generation
            self._cancel = threading.Event()
            details = {'checking':'正在检查版本…','downloading':'正在下载并校验…',
                       'preparing':'正在准备独立升级程序…'}
            self._status = UpdateStatus(state, details[state],
                                        release=self._status.release if state in ('downloading','preparing') else None,
                                        generation=generation)
            self.thread = threading.Thread(target=self._run, args=(operation, generation, self._cancel),
                                           name='helper-update', daemon=True)
            self.thread.start()
            return True

    def _run(self, operation, generation, cancelled):
        try:operation(generation, cancelled)
        except Exception as failure:
            # 只展示本模块边界定义的错误，其余网络/文件异常不泄露底层请求内容。
            from .updater import UpdateError
            detail = str(failure) if isinstance(failure, UpdateError) else '操作失败，请检查网络或目录权限后重试。'
            self._publish(generation, state='error', detail=detail, staged=None)

    def _run_check(self, generation, cancelled):
        result = self._check(self.version)
        with self._lock:
            published = self._publish(generation, state='available' if result.available else 'current',
                                      detail=('有新版本可下载。' if result.available else
                                              '当前版本无需升级。' if result.release is not None else '暂未找到可用的正式发行信息。'),
                                      release=result.release, staged=None)
            if published and result.release is not None:
                self._available_release = result.release if result.available else None

    def _run_download(self, generation, cancelled, release):
        def progress(downloaded, total):
            self._publish(generation, downloaded=max(0, int(downloaded)), total=max(0, int(total)))
        staged = self._download(release, staging_root=self.staging_root, cancel=cancelled, progress=progress)
        with self._lock:
            if self._publish(generation, state='downloaded', detail='下载与校验完成，点击“重启并更新”安装。', staged=staged):
                self._owned_stage = staged
            else:self._queue_cleanup(staged)

    def cancel(self):
        with self._lock:
            if self._status.state in ('preparing','restarting'):return False
            self._cancel.set()
            self._generation += 1
            self._release_owned_stage()
            self._status = UpdateStatus('cancelled', '本次更新任务已取消。', generation=self._generation)
            return True

    def close(self, *, wait=True):
        with self._lock:
            self._closed = True
            self._cancel.set()
            self._release_owned_stage()
        if wait and self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(1)
        if wait and self.cleanup_thread is not None and self.cleanup_thread is not threading.current_thread():
            self.cleanup_thread.join(1)
