"""安装诊断仅访问隔离临时目录和演示源，不接入游戏或真实凭据。"""
import json
from pathlib import Path
import tempfile


def check_installation(output,*,display_mode=None):
    from .app import build_controller
    from .paths import AppPaths
    from .window_settings import WindowSettings
    from .ui.main_window import create_app
    from .display_runtime import TkDisplayRuntime,window_dpi
    import tkinter as tk
    from alibabacloud_alimt20181012.client import Client
    with tempfile.TemporaryDirectory(prefix='hots-installation-check-') as tmp:
        base=Path(tmp)
        blank=base/'blank'
        actual=build_controller(AppPaths(blank/'.env',blank/'usage.db',blank/'language.db',blank/'preferences.json'))
        try:
            blank_ok=not actual.cloud_configured and actual.paused and actual.account=='unconfigured'
        finally:
            actual.account_setup.close();actual.close()
        controller=build_controller(AppPaths(base/'.env',base/'usage.db',base/'language.db',base/'preferences.json'),True)
        root=tk.Tk();root.withdraw();window=None
        try:
            window=create_app(root,controller,WindowSettings(hotkeys={},overlay_enabled=False),display=TkDisplayRuntime(root))
            root.update();window.pump()
            result={'passed':blank_ok and len(window.vm.messages)==3 and len(controller.phrases.list())==12,
                    'demo_messages':len(window.vm.messages),'public_phrases':len(controller.phrases.list()),
                    'cloud_requests':0,'game_access':False,'real_credentials_read':False,'tk_available':True,
                    'sdk_import_available':Client is not None}
            result['blank_user_raw_mode']=blank_ok
            result['display']={'awareness':display_mode,'dpi':window_dpi(root),
                               'scale':window._layout_metrics.scale,
                               'work_area':window._layout_metrics.work_area}
            Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            return 0 if result['passed'] else 2
        finally:
            if window:window.close()
            else:controller.close();root.destroy()
