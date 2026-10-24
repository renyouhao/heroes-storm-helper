"""入口导入无副作用；仅 main 创建窗口。"""
import argparse
from pathlib import Path
import tempfile
import sys
from . import APP_NAME
from .paths import AppPaths
from .config import ConfigError
from .controller import ChatController
from .acquisition.persistent import PersistentChatSource
from .translation.aliyun import AliyunEngine
from .models import TranslationResult,ChatEvent,AppEvent
from .instance import SingleInstance
from .phrases import PhraseBook,validate_phrase
from .language_store import decode_import

PROJECT_ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))


class DemoEngine:
    provider_id='demo'; config_version='offline-v1'
    def translate(self,job): return TranslationResult(job.job_id,'[离线演示] '+job.text,None,None,0)
    def close(self): pass


class DemoSource:
    selected_pid=None
    def process_finder(self):return []
    def start(self,emit):
        for i,text in enumerate(('hello team','같이 가요','ㄱㄱ')):
            emit(AppEvent('chat',0,ChatEvent(str(i),0,'团队','演示队友','00:01',text)))
    def stop(self):pass
    def reconnect(self):pass


def build_controller(paths,demo=False,diagnostics=None):
    saved=None;notice=''
    if demo:
        engine,source=DemoEngine(),DemoSource();account='main'
    else:
        from .credentials import CredentialStore
        from .identity import IdentityClient
        from .account_setup import AccountSetup,UnconfiguredEngine
        store=CredentialStore(paths.credentials_file)
        try:saved=store.load()
        except ConfigError:notice='本机加密凭据无法读取，请在设置中重新配置自己的密钥。'
        if saved and diagnostics is not None:
            diagnostics.register_secrets(saved.credentials.access_key_id,saved.credentials.access_key_secret)
        engine=AliyunEngine(saved.credentials) if saved else UnconfiguredEngine()
        source=PersistentChatSource(**({'diagnostics':diagnostics} if diagnostics is not None else {}))
        account=saved.account if saved else 'unconfigured'
    controller=ChatController(source,engine,account,diagnostics=diagnostics)
    controller.is_demo=demo
    controller.cloud_configured=bool(demo or saved)
    controller.startup_notice=notice
    if not demo:
        controller.account_setup=AccountSetup(controller,store,IdentityClient(),AliyunEngine,saved)
        if not saved:controller.pause_translation(True)
    controller.preferences_path=paths.preferences_file
    try:
        controller.phrases=PhraseBook(paths.language_file)
        data=(PROJECT_ROOT/'data'/'phrases.json').read_text(encoding='utf-8')
        controller.phrases.store.seed_once('phrases',decode_import(data,'phrases',validate_phrase))
        controller.set_local_resolver(controller.phrases.resolve)
    except BaseException:
        controller.close();raise
    return controller


def main(argv=None):
    parser=argparse.ArgumentParser(description=APP_NAME)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--demo',action='store_true',help='离线演示，不读取游戏或凭据')
    parser.add_argument('--legacy-ui',action='store_true',help='使用保留的 Tk 原生主界面')
    mode.add_argument('--check-desktop-installation',type=Path,metavar='REPORT',help='隔离检查新版界面资源和原生服务')
    mode.add_argument('--check-desktop-rendering',type=Path,metavar='REPORT',help='隔离检查实际WebView2页面与网页桥接')
    mode.add_argument('--check-installation',type=Path,metavar='REPORT',help='隔离安装自检并写入报告，不连接游戏或云')
    mode.add_argument('--apply-update',type=Path,metavar='PLAN',help='运行独立升级事务，不打开助手或连接游戏')
    mode.add_argument('--check-cloud-configuration',type=Path,nargs=2,metavar=('CREDENTIALS','REPORT'),
                      help='显式使用指定凭据文件，在临时目录测试一键配置和重载翻译；共 4 字符，不访问游戏或现用配置')
    args=parser.parse_args(argv)
    if args.apply_update:
        from .update_install import apply_update
        return apply_update(args.apply_update)
    if args.check_cloud_configuration:
        from .cloud_diagnostic import check_cloud_configuration
        return check_cloud_configuration(*args.check_cloud_configuration)
    from .display_runtime import enable_process_dpi_awareness,TkDisplayRuntime
    display_mode=enable_process_dpi_awareness()
    if args.check_installation:
        from .installation import check_installation
        return check_installation(args.check_installation,display_mode=display_mode)
    if args.check_desktop_installation:
        from .desktop import check_desktop_installation
        return check_desktop_installation(args.check_desktop_installation)
    if args.check_desktop_rendering:
        from .desktop import check_desktop_rendering
        return check_desktop_rendering(args.check_desktop_rendering)
    if not args.legacy_ui:
        from .desktop import run_desktop,DesktopError
        try:return run_desktop(demo=args.demo)
        except DesktopError as failure:
            from .startup import report_startup_failure
            report_startup_failure(failure,message=str(failure))
            return 2
    mutex=SingleInstance()
    if not mutex.acquire(): print('助手已经运行。'); return 1
    controller=None;diagnostics=None;diagnostic_hooks=None
    temporary=tempfile.TemporaryDirectory(prefix='hots-demo-') if args.demo else None
    try:
        paths=AppPaths.from_root(PROJECT_ROOT)
        if temporary:
            base=Path(temporary.name)
            paths=AppPaths(base/'.env',base/'language.sqlite3',base/'preferences.json')
        if not args.demo:
            from .diagnostics import DiagnosticRecorder
            from .diagnostic_hooks import DiagnosticHooks
            diagnostics=DiagnosticRecorder(paths.diagnostics_dir)
            diagnostic_hooks=DiagnosticHooks(diagnostics)
            diagnostics.record('app.start',stage='startup')
        controller=build_controller(paths,args.demo,diagnostics=diagnostics)
        import tkinter as tk
        from .ui.main_window import create_app
        from .window_settings import WindowSettings
        root=tk.Tk()
        if diagnostic_hooks is not None:diagnostic_hooks.attach_root(root)
        create_app(root,controller,WindowSettings.load(paths.preferences_file),
                               display=TkDisplayRuntime(root),enable_repeat_hotkey=not args.demo,
                               enable_talent_helper=not args.demo,enable_update_check=not args.demo)
        root.mainloop()
        return 0
    except ConfigError as failure:
        if diagnostic_hooks is not None:diagnostic_hooks.capture('startup',failure)
        from .startup import report_startup_failure
        report_startup_failure(failure,message='助手配置无法读取，请检查本机配置或重新配置账户。')
        return 2
    except Exception as failure:
        if diagnostic_hooks is not None:diagnostic_hooks.capture('main',failure)
        raise
    finally:
        if controller:
            if hasattr(controller,'account_setup'):controller.account_setup.close()
            controller.close()
        mutex.close()
        if temporary:temporary.cleanup()
        if diagnostic_hooks is not None:diagnostic_hooks.close()
        if diagnostics is not None:
            diagnostics.record('app.stop',stage='shutdown')
            diagnostics.close()


if __name__=='__main__':
    from multiprocessing import freeze_support
    freeze_support()
    raise SystemExit(main())
