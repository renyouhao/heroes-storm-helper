"""React 桌面壳：WebView2 与原生游戏运行时各自保留独立的主事件循环。"""
from pathlib import Path
import json
import multiprocessing
import sys
import threading
import time
import uuid

from . import APP_NAME,__version__,RELEASE_CHANNEL,DISPLAY_VERSION
from .instance import SingleInstance
from .ui.brand_icon import assistant_icon_path


class DesktopError(RuntimeError):
    pass


def webview2_available():
    """只读检查 WebView2 与其 .NET 前置条件，不触发旧 IE 渲染器初始化。"""
    if sys.platform!='win32':return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full') as key:
            if int(winreg.QueryValueEx(key,'Release')[0])<394802:return False
    except (OSError,ValueError,TypeError):return False
    runtime='{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        for prefix in (r'SOFTWARE\Microsoft\EdgeUpdate\Clients',r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients'):
            try:
                with winreg.OpenKey(hive,prefix+'\\'+runtime) as key:
                    if int(str(winreg.QueryValueEx(key,'pv')[0]).split('.')[0])>=86:return True
            except (OSError,ValueError,TypeError):continue
    return False


def frontend_entry(root=None):
    base=Path(root) if root is not None else Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))
    entry=base/'frontend'/'dist'/'index.html'
    if not entry.is_file():
        raise DesktopError('缺少新版界面文件。请先运行 安装依赖.ps1，或在 frontend 目录执行 npm ci 和 npm run build。')
    return entry


class NativeClient:
    """单通道串行请求；超时不重发可能产生副作用的操作。"""
    def __init__(self,connection,process,timeout=12):
        self.connection,self.process=connection,process
        self.timeout=timeout
        self._lock=threading.Lock()
        self._close_lock=threading.Lock()
        self._closed=False

    def request(self,command,payload=None):
        with self._lock:
            if self._closed or not self.process.is_alive():
                raise DesktopError('原生服务已停止，请关闭助手后重新启动。')
            request_id=uuid.uuid4().hex
            try:
                self.connection.send({'request_id':request_id,'command':command,'payload':payload or {}})
                if not self.connection.poll(self.timeout):
                    raise DesktopError('原生服务响应超时。本次操作不会自动重试，请核对当前状态。')
                reply=self.connection.recv()
                # 前一个调用超时后可能迟到，不能把它的成功回执归给新请求。
                while isinstance(reply,dict) and reply.get('request_id')!=request_id:
                    if not self.connection.poll(self.timeout):
                        raise DesktopError('原生服务响应超时，请核对当前状态后再操作。')
                    reply=self.connection.recv()
            except (EOFError,BrokenPipeError,OSError) as failure:
                raise DesktopError('与原生服务的连接已断开，请重新启动助手。') from failure
            if not isinstance(reply,dict) or reply.get('request_id')!=request_id:
                raise DesktopError('原生服务返回了无法识别的结果。')
            return reply

    def close(self):
        with self._close_lock:
            if self._closed:return
            try:
                if self.process.is_alive():self.request('shutdown')
            except DesktopError:pass
            finally:
                self._closed=True
                self.connection.close()
                self.process.join(timeout=6)
                if self.process.is_alive():
                    self.process.terminate()
                    self.process.join(timeout=3)


class DesktopApi:
    """网页只获得状态和白名单命令；不暴露 Python 对象或任意方法执行。"""
    def __init__(self,client):
        self._client=client
        self._window=None
        self._maximized=False
        self._topmost=None

    def snapshot(self):
        try:
            reply=self._client.request('snapshot')
            if not reply.get('ok'):raise DesktopError(reply.get('error','无法读取助手状态。'))
            # 兼容原生协议以 result 或 snapshot 包装快照。
            snapshot=reply.get('snapshot',reply.get('result',reply.get('data',reply)))
            preferences=snapshot.get('management',{}).get('preferences',{}) if isinstance(snapshot,dict) else {}
            topmost=preferences.get('topmost')
            if self._window is not None and type(topmost) is bool and topmost!=self._topmost:
                self._window.on_top=topmost
                self._topmost=topmost
            return snapshot
        except DesktopError as failure:
            return {'error':str(failure),'connected':False}

    def command(self,name,payload=None):
        if not isinstance(name,str) or (payload is not None and not isinstance(payload,dict)):
            return {'ok':False,'error':'操作参数格式不正确。'}
        try:
            if name in ('minimize','maximize','close'):
                if self._window is None:raise DesktopError('窗口尚未就绪。')
                if name=='minimize':self._window.minimize()
                elif name=='maximize':
                    if self._maximized:self._window.restore()
                    else:self._window.maximize()
                    self._maximized=not self._maximized
                else:self._window.destroy()
                return {'ok':True}
            from .desktop_protocol import COMMANDS
            if name not in COMMANDS or name in ('snapshot','shutdown'):
                return {'ok':False,'error':'不支持的操作。'}
            return self._client.request(name,payload)
        except (DesktopError,ValueError) as failure:
            return {'ok':False,'error':str(failure)}


def start_native(demo=False):
    from .desktop_runtime import run_native
    context=multiprocessing.get_context('spawn')
    connection,child=context.Pipe(duplex=True)
    process=context.Process(target=run_native,args=(child,demo),name='hots-native-runtime')
    process.start()
    child.close()
    return NativeClient(connection,process)


def run_desktop(*,demo=False):
    entry=frontend_entry()
    try:import webview
    except ImportError as failure:
        raise DesktopError('未安装新版界面依赖，请运行 安装依赖.ps1。') from failure
    if not webview2_available():
        raise DesktopError('新版界面需要 Microsoft Edge WebView2 Runtime 和 .NET Framework 4.6.2 或以上。'+
                           '请安装 WebView2 Runtime，或运行 启动助手.ps1 -Legacy 临时使用旧界面。')
    mutex=SingleInstance()
    if not mutex.acquire():
        print('助手已经运行。请先关闭原窗口。')
        mutex.close()
        return 1
    client=None
    try:
        client=start_native(demo)
        api=DesktopApi(client)
        window=webview.create_window(APP_NAME+' · '+DISPLAY_VERSION+(' · 离线演示' if demo else ''),
                                    str(entry),js_api=api,width=1440,height=960,min_size=(760,560),
                                    frameless=True,easy_drag=False,shadow=True,text_select=True,
                                    background_color='#F3F1FF')
        api._window=window
        window.events.closed+=client.close
        # 明确固定 WebView2；不退回无法正确呈现现代 CSS 的旧 IE 渲染器。
        webview.start(gui='edgechromium',http_server=True,debug=False,private_mode=True,
                      icon=str(assistant_icon_path()))
        return 0
    except Exception as failure:
        raise DesktopError('新版界面启动失败。请确认已安装 Microsoft Edge WebView2 Runtime。'+
                           '如需临时使用旧界面，可运行 启动助手.ps1 -Legacy。') from failure
    finally:
        if client is not None:client.close()
        mutex.close()


def check_desktop_installation(output):
    """检查新前端资源和独立原生进程；不操作游戏、凭据或云翻译。"""
    entry=frontend_entry()
    import webview
    runtime_available=webview2_available()
    client=start_native(True)
    try:
        reply=client.request('snapshot')
        result={'passed':bool(reply.get('ok')) and runtime_available,'frontend_available':entry.is_file(),
                'pywebview_available':webview is not None,'native_process_available':client.process.is_alive(),
                'webview2_runtime_available':runtime_available,
                'real_credentials_read':False,'game_access':False,'cloud_requests':0,
                'webview_rendering_verified':False}
        Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return 0 if result['passed'] else 2
    finally:client.close()


def check_desktop_rendering(output):
    """Load the packaged page and exercise its JS bridge in an isolated demo.

    The hidden test window owns its WebView2. No desktop input, game access,
    user preferences, credentials or clipboard operations are required.
    """
    result={'passed':False,'webview_rendering_verified':False,
            'version':__version__,'release_channel':RELEASE_CHANNEL,'display_version':DISPLAY_VERSION,
            'real_credentials_read':False,'game_access':False,'cloud_requests':0,
            'clipboard_written':False,'global_hotkeys':False}
    client=None
    try:
        import webview
        if not webview2_available():
            raise DesktopError('未找到兼容的 WebView2 Runtime。')
        entry=frontend_entry()
        if not assistant_icon_path().is_file():
            raise DesktopError('缺少程序图标资源。')
        client=start_native(True)
        api=DesktopApi(client)
        loaded=threading.Event()
        window=webview.create_window('界面安装自检 · 离线演示',str(entry),js_api=api,
                                     width=1080,height=720,hidden=True,focus=False,
                                     frameless=True,easy_drag=False,background_color='#F3F1FF')
        api._window=window
        window.events.loaded+=loaded.set
        window.events.closed+=client.close

        def verify_page():
            try:
                if not loaded.wait(20):
                    raise DesktopError('WebView2 页面加载超时。')
                deadline=time.monotonic()+18
                while time.monotonic()<deadline:
                    page=window.evaluate_js("""({
                        ready:document.readyState,
                        title:document.querySelector('h1')?.textContent,
                        rows:document.querySelectorAll('.message-row').length,
                        raven:document.body.innerText.includes('Raven'),
                        mochi:document.body.innerText.includes('Mochi'),
                        demoBadge:document.querySelector('.environment-label')?.innerText.includes('离线演示')===true,
                        theme:document.querySelector('.desktop')?.dataset.theme,
                        connectionError:document.body.innerText.includes('连接助手失败'),
                        bridge:typeof window.pywebview?.api?.snapshot==='function',
                        snapshot:window.__hotsInstallationSnapshot||null
                    })""")
                    if page and page.get('bridge'):
                        # Invoke through the actual JavaScript promise bridge,
                        # rather than calling DesktopApi.snapshot from Python.
                        window.evaluate_js("""if (!window.__hotsInstallationPending) {
                            window.__hotsInstallationPending=true;
                            void window.pywebview.api.snapshot().then(
                            s=>{window.__hotsInstallationSnapshot={
                                count:Array.isArray(s.messages)?s.messages.length:0,
                                demo:s.isDemo===true,error:!!s.error
                            }},
                            ()=>{window.__hotsInstallationSnapshot={error:true}}
                            ).finally(()=>{window.__hotsInstallationPending=false})
                        }""")
                    snapshot=(page or {}).get('snapshot') or {}
                    if (page and page.get('ready')=='complete' and page.get('title')=='实时消息'
                            and page.get('rows')==4 and page.get('raven') and page.get('mochi')
                            and page.get('bridge') and page.get('demoBadge') and page.get('theme')=='lavender'
                            and not page.get('connectionError')
                            and snapshot.get('count')==4 and snapshot.get('demo')
                            and not snapshot.get('error')):
                        result.update(passed=True,webview_rendering_verified=True,
                                      javascript_bridge_verified=True,page=page,
                                      backend='edgechromium',window_size=[1080,720],
                                      application_icon_set=bool(window.native.Icon.Handle.ToInt64()))
                        if not result['application_icon_set']:
                            raise DesktopError('主窗口图标未加载。')
                        break
                    time.sleep(.15)
                if not result['passed']:
                    result['page']=page
                    raise DesktopError('页面或网页桥接未能在期限内就绪。')
            except Exception as failure:
                result['passed']=False
                result['webview_rendering_verified']=False
                result['error']=str(failure)
                result['error_type']=type(failure).__name__
            finally:
                window.destroy()

        webview.start(verify_page,gui='edgechromium',http_server=True,debug=False,private_mode=True,
                      icon=str(assistant_icon_path()))
    except Exception as failure:
        result['passed']=False
        result['webview_rendering_verified']=False
        result['error']=str(failure)
        result['error_type']=type(failure).__name__
    finally:
        if client is not None:client.close()
        Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['passed'] else 2
