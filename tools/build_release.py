"""本机生成免安装目录包；只显式复制公开种子，不读取用户配置。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from hots_chat import APP_NAME,DISPLAY_VERSION,RELEASE_ARCHIVE_PREFIX,RELEASE_CHANNEL,__version__

ROOT=Path(__file__).resolve().parents[1]
NAME=APP_NAME
VERSION=__version__
PUBLIC_SEEDS=('phrases.json','talents_98348_zhCN.json','talent_catalog_source.json',
              'quests_98348_zhCN.json','talents_98348_enUS.json','quests_98348_enUS.json',
              'talent_catalog_source_enUS.json','assistant.ico')


def release_archive_name(version=None,channel=None):
    version=VERSION if version is None else version
    channel=RELEASE_CHANNEL if channel is None else channel
    suffix='-prerelease' if channel=='prerelease' else ''
    return RELEASE_ARCHIVE_PREFIX+'-'+version+suffix+'-Windows-x64.zip'


def build_frontend(root):
    """Rebuild the local UI before freezing; never ship a stale/missing dist."""
    frontend=Path(root)/'frontend'
    if not (frontend/'package-lock.json').is_file():
        raise ValueError('缺少 frontend/package-lock.json，请先运行 安装依赖.ps1。')
    npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
    if not npm:raise ValueError('构建主界面需要 Node.js/npm，请先安装 Node.js LTS 并重新打开终端。')
    subprocess.run([npm,'ci'],cwd=frontend,check=True)
    subprocess.run([npm,'run','build'],cwd=frontend,check=True)
    dist=frontend/'dist'
    if not (dist/'index.html').is_file() or not any((dist/'assets').glob('*.js')):
        raise ValueError('前端构建未生成完整的 frontend/dist/index.html 和 assets/*.js。')
    return dist


def desktop_freeze_options(frontend_dist):
    """Only the Windows WebView2 backend and its managed/native bridges."""
    options=['--add-data',str(frontend_dist)+os.pathsep+'frontend/dist']
    for package in ('webview','pythonnet','clr_loader'):
        options+=['--collect-data',package,'--collect-binaries',package]
    for module in ('clr','webview.platforms.winforms','webview.platforms.edgechromium'):
        options+=['--hidden-import',module]
    for module in ('webview.platforms.qt','webview.platforms.cef','webview.platforms.gtk',
                   'webview.platforms.android','webview.platforms.cocoa','webview.platforms.mshtml',
                   'PyQt5','PyQt6','PySide2','PySide6','cefpython3'):
        options+=['--exclude-module',module]
    return options


def stage_public_data(root, destination):
    for name in PUBLIC_SEEDS:
        target=Path(destination)/'data'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(Path(root)/'data'/name,target)


def stage_demo_launcher(bundle):
    """A double-click offline entry; resolve the sole root EXE without codepage issues."""
    bundle=Path(bundle)
    script=('@echo off\nsetlocal\n'
            'for %%f in ("%~dp0*.exe") do (\n'
            '    if exist "%%~ff" (\n'
            '        start "" "%%~ff" --demo\n'
            '        exit /b 0\n'
            '    )\n)\n'
            'echo The application executable was not found.\npause\nexit /b 1\n')
    (bundle/'查看界面效果.cmd').write_bytes(script.replace('\n','\r\n').encode('ascii'))
    note=bundle/'使用说明.txt'
    if note.is_file():
        content=note.read_text(encoding='utf-8-sig')
        instruction='查看界面效果：双击“查看界面效果.cmd”进入离线演示，无需游戏或Key；普通使用双击“风暴英雄助手.exe”。\n'
        if instruction not in content:
            title,separator,body=content.partition('\n\n')
            note.write_text(title+separator+instruction+body,encoding='utf-8-sig')


def verify_bundle(bundle):
    result=[]
    for path in sorted(Path(bundle).rglob('*')):
        if not path.is_file():continue
        relative=path.relative_to(bundle)
        parts=[p.lower() for p in relative.parts]
        if any(p.startswith('.env') or p.endswith(('.dpapi','.db','.log','.sqlite','.sqlite3','.initialized'))
               or '.sqlite' in p or p in ('probes','benchmarks','验收报告','preferences.json') for p in parts):
            raise ValueError('发行目录包含本地状态或诊断文件，已阻止打包。')
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        result.append({'path':relative.as_posix(),'bytes':path.stat().st_size,'sha256':digest})
    if not any(row['path']==NAME+'.exe' for row in result):raise ValueError('缺少程序入口。')
    return result


def verify_archive(path,files):
    """Read the written ZIP back, checking its entries against the bundle manifest."""
    expected={NAME+'/'+row['path']:row for row in files}
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist())!=len(expected) or set(archive.namelist())!=set(expected):
            raise ValueError('发行 ZIP 文件清单不一致。')
        for name,row in expected.items():
            with archive.open(name) as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            if archive.getinfo(name).file_size!=row['bytes'] or digest!=row['sha256']:
                raise ValueError('发行 ZIP 文件校验失败。')


def _plain_path(path):
    # Windows junctions are reparse points too; never traverse either kind of link.
    for item in (path,*path.parents):
        if item.is_symlink():return False
        if item.exists() and getattr(item.lstat(),'st_file_attributes',0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return False
    return True


def _safe_cleanup_tree(path,scope):
    if path==scope or not path.is_relative_to(scope) or not _plain_path(path):return False
    if path.is_dir():
        for child in path.iterdir():
            if not _safe_cleanup_tree(child,scope):return False
    return True


def _archive_name(name):
    prefixes='|'.join(re.escape(prefix) for prefix in (RELEASE_ARCHIVE_PREFIX,NAME))
    return bool(re.fullmatch(r'(?:'+prefixes+r')-\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?-Windows-x64\.zip(?:\.tmp|\.sha256)?',name))


def _release_artifact(path):
    if path.is_file():return _archive_name(path.name) or path.name=='发行校验.json'
    if not path.is_dir():return False
    # These markers identify a source checkout, even if someone put a ZIP beside it.
    if any((path/name).exists() for name in ('.git','pyproject.toml','src')):return False
    return (path.name.startswith('hots-release-')
            or any(child.is_file() and _archive_name(child.name) for child in path.iterdir())
            or ((path/(NAME+'.exe')).is_file() and path.name.startswith(NAME+'-')))


def cleanup_previous_releases(output,keep):
    """Prune known build products only, after the new archive has passed verification."""
    output=Path(os.path.abspath(output))
    releases=Path(os.path.abspath(ROOT/'releases'))
    keep={Path(os.path.abspath(path)) for path in keep}
    if not _plain_path(output):return
    # A custom external output owns only its own products, never its parent directory.
    scopes=[output]
    if output!=releases and output.is_relative_to(releases):scopes.append(releases)
    for scope in scopes:
        if not _plain_path(scope):continue
        for candidate in scope.iterdir():
            if any(item==candidate or item.is_relative_to(candidate) for item in keep):continue
            if _safe_cleanup_tree(candidate,scope) and _release_artifact(candidate):
                if candidate.is_dir():shutil.rmtree(candidate)
                else:candidate.unlink()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'releases')
    args=parser.parse_args(argv)
    frontend_dist=build_frontend(ROOT)
    args.output.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='hots-release-',dir=args.output))
    stage_public_data(ROOT,work/'public')
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--windowed','--noupx',
             '--name',NAME,'--paths',str(ROOT/'src'),'--distpath',str(work/'dist'),
             '--workpath',str(work/'build'),'--specpath',str(work)]
    command+=['--manifest',str(ROOT/'packaging'/'highdpi.manifest')]
    command+=['--icon',str(ROOT/'data'/'assistant.ico')]
    for name in PUBLIC_SEEDS:
        command+=['--add-data',str(work/'public'/'data'/name)+os.pathsep+'data']
    command+=desktop_freeze_options(frontend_dist)
    command.append(str(ROOT/'packaging'/'launcher.py'))
    subprocess.run(command,cwd=ROOT,check=True)
    bundle=work/'dist'/NAME
    note=(f'{NAME} {DISPLAY_VERSION}（程序 {VERSION}） · Windows x64 免安装版\n\n'
          'v2.5 更新：修复真实【征召团队】使用独立原生大厅会话、旧显示历史入口无法接收的问题。对局来源仅接团队/所有玩家，原生大厅来源接队伍/密语/征召团队；先核对游戏代码、当前房间及频道类别，只接确认上下文后产生的新征召记录，拒收全部征召与未知会话，不依赖局内聊天时间显示开关。\n'
          '本机只读监听已接到征召测试标记，相关合成测试覆盖接收、翻译、界面及日志导出；朋友机器的完整新包与真实云译文仍需复测。原生诊断native_lobby_schema=2，新增会话类型、上下文门禁、拒绝、去重及累计读取计数；扫描和读取器ID连接到消息、队列与翻译日志，检查次数可能重复，不等于交付消息数。\n'
          '保留v2.4的无时间戳团队/所有玩家显示历史采集、完整阿里云/DeepSeek服务选择、账户连接、余额和上下文设置；对局采集日志capture_schema=2。没有原生时间的显示历史消息时间字段留空，不编造对局时间。\n'
          'v2.3 更新：修复桌面升级只退出原生子进程导致连接中断、文件和单实例锁未释放的问题；升级程序等待整个宿主退出，准备阶段显示校验、复制和启动进度；正常启动后台检查一次更新，下载与安装仍需主动操作。旧版卡住时请退出整个助手，完整解压新包到新目录后启动，保留 _internal。真实冻结包跨版本升级仍待验收。\n'
          '此前 v2.2 更新：修复下载标记导致内置托管运行库无法加载时无界面的启动问题，新增独立启动错误提示与有界日志；修复合法天赋ID中的 ! 与撇号被拒绝、影响精灵龙及雷诺和祖尔金的问题。精灵龙21项天赋与七层选择已通过合成验证，真实游戏仍待复测。\n'
          '此前v2.1：统一React管理弹层，应用主窗口字号与置顶，改进状态与更新提示点、Markdown发布说明、原生Ctrl+T短句候选、有效译文复制和失败恢复，并归并同一R天赋与CD覆盖层。\n'
          '沿用v2.0引入的React/WebView2桌面主界面、柔雾紫主题、紫色图标、完整模型解码分支和诊断回放。\n'
          f'解压整个文件夹，双击“{NAME}.exe”，无需安装Python或Node.js；需Microsoft Edge WebView2 Runtime。\n'
          '启动时仅为当前发行包内 Python.Runtime.dll 和 WebView2 Core/WinForms 托管运行库解除 Windows 下载标记，不扫描其它目录、不更改 DLL 内容。\n'
          '若启动失败，独立于 WebView2 的系统弹窗会给出错误类别与启动日志位置；%LOCALAPPDATA%/HotsChatHelper/startup.log 仅保留最近一次失败，最大128KiB，包含异常链，不主动读取凭据或采集局部变量，分享前请检查异常内容。正常启动不创建此日志；它与默认关闭的功能诊断日志分开。\n'
          '若文件权限阻止解除下载锁定：核验官方 ZIP 后，右键原 ZIP→属性→解除锁定，再重新解压完整文件夹（含 _internal）；已经解压时需对整个应用目录解除锁定，只解锁 EXE 不够。\n'
          '主界面使用React + TypeScript + CSS，由pywebview/WebView2加载本地资源，不依赖开发服务器。\n'
          '主界面、偏好设置、账户、更新和短句编辑统一使用React，管理操作共用一个页内弹层，重复点击复用当前界面；打开设置不会带出快捷输入。\n'
          '采集、翻译、持久化和游戏输入继续由Python处理；游戏内字幕与Ctrl+T快捷输入保留原生Tk，-Legacy兼容入口保留原Tk界面。\n'
          '缺少WebView2时按启动提示安装官方Runtime，不会静默切换旧浏览器。源码可用启动助手.ps1 -Legacy进入旧界面。\n'
          '首次使用从主界面“选择翻译服务”或“偏好设置→连接→配置账户”进入；新用户先选阿里云机器翻译或DeepSeek V4.1 Flash，再填写自己的凭据。只选卡片不发送请求，也不立即切换当前服务。\n'
          '阿里云：在自己的账号开通机器翻译通用版，给RAM用户alimt:TranslateGeneral权限，填写同一把AccessKey ID/Secret，不填写网页登录账号密码。DeepSeek：在官方平台创建自己的API Key，回助手填写一个API Key字段。点击“连接并开始使用”，验证与“你好”翻译测试成功后才分别加密保存并启用。\n'
          '原有阿里云账户继续兼容；切换服务时可验证并使用对应的已保存账户，无需重输。翻译失败不自动切换服务。取消未完成的配置后云翻译保持暂停，回账户页验证使用已保存账户后恢复。\n'
          '助手不建立本地月度用量账本、不要求填写本月其他用量，也没有本地90万字符上限。费用以当前服务账户的实际账单为准，旧用量文件保留但不再读取。\n'
          '查询免费额度/用量：阿里云在偏好设置→连接或账户页打开机器翻译控制台，由你登录查看字符额度、用量和费用，助手不自动读取。查询账户余额：DeepSeek在后台查询，按返回币种显示总余额、赠送余额、充值余额与更新时间，不换算为剩余字符。\n'
          'DeepSeek使用deepseek-flash（V4.1 Flash），关闭思考；“发送同会话最近3条原文”默认开启，可关闭，已连接时点击“保存上下文设置”应用。最多附同频道或同一密语联系人最近三条匿名原文，不附昵称；手动中文回复与Ctrl+T只发送本次输入。\n'
          '处理时字段暂时禁用；关闭会取消尚未完成的操作，已经完成的保存或删除保持当前结果。测试失败或超时后可修改配置并手动重试。\n'
          '失败提示当前阶段、安全错误类别或可识别云错误码；未知错误不直接展示底层内容。阿里云检查Key配对、服务开通和RAM权限，DeepSeek检查API Key及账户可用余额；两者都需网络可用。\n'
          '配置目录自动创建，无需拷贝AppData或另装SDK；首次不必分两步操作。\n'
          '可跳过云配置，只接收原文。两家密钥分别保存在当前Windows用户绑定的本机加密文件；全局界面状态快照不含完整AccessKey ID/Secret或DeepSeek API Key。\n'
          '无边框游戏模式；Ctrl+T中文翻译并复制，再由你粘贴发送。空输入显示少量收藏短句，输入中文后查找本地匹配候选。\n'
          '短句候选：第一次Tab进入候选列表，方向键在列表内选择；第二次Tab或鼠标点击只填入中文，Enter才翻译并复制。输入法选字时不抢候选快捷键，Ctrl+Tab切换英语/韩语。\n'
          '已校对、中文精确匹配且当前语言译文唯一时复用既有本机译文；未校对、缺少译文或存在冲突时仍需翻译。修改短句中文或译文后需重新核对。\n'
          '界面统一为柔雾紫单主题，不再提供配色切换；旧配色配置自动迁移，原有偏好继续保存在本机。\n'
          '悬浮窗采用B方案：同步柔雾紫语义配色，保留原布局、语言下拉和翻译复制操作；透明字幕保持白字暗边。EXE、任务栏与窗口标题统一使用紫色六角旋涡标志。\n'
          '主窗侧边导航提供实时消息/常用短句/开箱助手/动作连按/天赋辅助/双击播报，底部提供偏好设置与检查更新；小窗口自动调整间距与内容排列。\n'
          '偏好设置→外观：内容字号10–32实际应用于React消息和输入文字，“主窗口保持置顶”应用于可见WebView窗口，可选标准/精简布局。主窗口透明度和位置锁定目前只在-Legacy兼容界面提供。\n'
          '双击播报默认关闭：开启后在游戏Tab天赋页双击一个已选天赋，说明玩家、英雄、真实天赋等级、名称与一句关键效果，发到己方团队；统计页点哪项只说哪项，0可说，破折号不说，不加排名或评价。短效果离线提供，无需翻译Key或AI。\n'
          '双击播报页可选择“发给队友的播报语言”：中文或English，默认中文，选择后保存，重启沿用。天赋、英雄、基础任务、大招名称与固定统计文案按离线英文资料输出；英文昵称保留；中文及其他非拉丁文字昵称（包括本地化电脑标签）以Player加可信槽位序号代称，同时显示英文英雄名，不猜译昵称或电脑身份，不调用云翻译。切换会取消尚未发送的旧动作，下一次双击使用新语言；英文资料缺失时不猜译、不混发中文或内部ID。此设置不改变聊天翻译目标，也不代表已验证英文游戏客户端。\n'
          'Tab天赋提醒以“注意我方/敌方”表达；任务天赋同条附当前层数，基础任务图标可独立双击播报。真实0层可播，已完成状态不把残留数字当最终层数；未知或未跟踪数值不播。基础任务名称按精确英雄/任务ID离线匹配，缺中文名时使用基础任务序号。\n'
          'Tab我方大招图标可双击播报“冷却显示：X秒”，忠实报告专用标签的正数计时；计时可能是充能恢复，不代表当前不可释放或X秒后可释放，不猜充能次数。隐藏标签、0、未选大招或未验证状态不推断已就绪；猎空专用充能界面暂不播报，不读取敌方大招冷却。\n'
          '同一大招的已选天赋与冷却层重叠时，按玩家、技能和原生位置归并为一个冷却目标；无可信冷却时保留普通天赋行为，其他不同目标重叠仍拒绝。\n'
          'v2.1修复后的实机只读核验目前仅确认萨拉塔斯试用中45秒对应唯一冷却命中，最终聊天发送仍待用户反馈，不代表全英雄或完整发送流程通过。\n'
          '开启诊断日志后同时记录任务/冷却原生状态、数值核验与固定失败原因、模型分支和字段代码校验、窗口与渲染尺寸、候选与命中；导出ZIP支持纯业务与UI数值判定回放。日志总上限默认100MiB，包含聊天/译文；轮转或丢弃会标明，回放不重连游戏或重发聊天。\n'
          '保留游戏默认Ctrl+Enter团队快捷键；双击后松开鼠标按键并保持前台。聊天框/菜单或开箱/连按/快捷输入/升级准备期间暂停。先核对团队空框，输入后逐字回读再提交；不改剪贴板或覆盖草稿。失败不自动重试，未提交文字按Esc取消；同项3秒、全局2秒限频，关开不重置。\n'
          '双击稳定性：修复慢读取将正常鼠标松开误判为超时，以及战斗统计数值正常更新导致整板失效。等待松开时不重复读取整板；只容忍数值变化，目标归属、格子位置、对局和输入环境仍须确认；过期动作不复活，失败不补发。\n'
          '兼容带Windows注入标记的桌面点击，忽略助手自身标记的事件。未识别、取消或限频原因请看助手的“双击播报”页；主窗口底部的大厅/对局文字仅表示聊天采集来源。Tab没关就退出对局也会使旧待发动作失效，新局重新打开面板并双击。\n'
          '本轮修复空闲F8连按监听与双击播报争用输出锁，造成文字缓慢出现或未发送的问题。热键与计时回调不等待其他发送器持有的输出栅栏；连按和播报输出仍互斥，取消仍等待已有输入结束。正文仍为批量输入，不改剪贴板。\n'
          '输入框短暂变化时在同一可信对局内有界重读，正文与发送按键不重复执行；换局、菜单、切出或新操作仍停止。文字留框时查看“双击播报”页的具体原因，先按Esc关掉草稿再双击；提示可能已发送时先查聊天记录。切回助手的普通操作保留上次结果；诊断开启后记录失败原因及回放所需的输入、状态和内容。\n'
          '当前源码新增启动自动检查：正常启动后后台检查一次官方稳定Release，发现新版点亮侧栏小点；演示和安装自检保持离线。仍可手动检查、下载并校验SHA256，再决定重启并更新；独立升级程序备份替换，失败尝试回滚；AppData密钥、短句与偏好保留。源码模式展示ZIP路径供手动使用，不替换源码。实际冻结包跨版本升级仍待验收。\n'
          '检查不自动打开更新界面；已确认有新版后，临时失败或取消任务保留提示点，成功确认无需升级时清除。下载后待安装期间提示保留。发布说明按标题、段落和列表排版，随内容区宽度换行，长内容可滚动阅读。\n'
          '动作连按：先选择S或Y+S，填写“按键间隔”并保存应用，回游戏单独F8开始、再F8取消；无需准备。两种动作都可设20–5000毫秒整数，分别保存，新配置均默认20；旧版保存值保留。S为两次S之间的间隔；Y+S为Y→间隔→S→间隔循环；保存配置结束旧动作。\n'
          '启动及运行沿用游戏底部被动状态条，提示“按 F8 取消”；取消后收起，不抢鼠标或输入焦点。\n'
          '偏好设置→快捷键可将连按开关改为F1–F12，提示同步实际按键；保存改键结束本次连按，下次新按即可开始，不保存运行状态。呼出快捷输入可取消勾选并保存，禁用后仍可从主界面“快捷输入”按钮进入。\n'
          '正式模式启动后监听快捷键，空闲不发送S；长按只切换一次。Esc、Enter、组合键、打开Ctrl+T或切出目标游戏窗口都会结束本次连按，回游戏不自动恢复；松开后新按快捷键可再次开始，此前Enter/Esc的状态不阻止下一次启动。\n'
          '连按每次发送所选动作中的一个键并完整抬起；聊天框开着时会输入S或交替输入Y和S。不自动点击鼠标，每次发送前核验真实游戏前台且同一HWND/PID。\n'
          '毫秒为目标发送间隔，实际受Windows调度和游戏响应影响；卡顿不追补，不保证精确次数或提升真实攻击速度。\n'
          '连按与开箱互斥：按F8后先显示启动状态条，关闭开箱发送门并等旧开箱线程退出，才允许发送所选按键；未能退出会取消并提示。活动期间聊天字幕暂藏且不续期。\n'
          '图片表情：正文中的完整img图片标签显示为[表情]，纯表情不调用云翻译，英韩混合消息保留正常翻译。\n'
          '诊断日志默认完全关闭，不启动写线程、不建日志文件。偏好设置→诊断→开启诊断日志→复现→导出诊断日志；后台完成后显示可复制的ZIP路径，请检查文件后自行分享。仅本次运行有效，下次启动仍关闭。开启后包含聊天原文、译文及翻译/天赋/双击播报复现记录，默认日志总上限100MiB，滚动覆盖最旧文件、保留7天；过载丢弃、覆盖和截断均标明。\n'
          '仅一个诊断开关，无详细模式或十分钟到期。已配置密钥递归脱敏，分享前检查聊天隐私。导出不含凭据文件/数据库/偏好、不上传、不调用云接口；开发者可用导出ZIP离线回放业务判断，外部游戏/Windows/云端返回以录制输入模拟。\n'
          '天赋辅助：默认关闭、不需要翻译Key。先在游戏标好爱心，保持快速选择首选天赋为Ctrl+反引号（Esc下方、数字1左侧），在助手天赋辅助页开启后回游戏；开关偏好保存。\n'
          '按真实英雄天赋层级检测待选，每层最多触发一次快捷键并回读确认；3秒未选中请检查爱心/快捷键并手选本层，下一层继续检测。关开开关、短暂未知不会重复刷本层；关闭停止读取与发送。\n'
          '组合键分阶段发送：Ctrl先按下约80毫秒，再按住反引号约80毫秒，最后释放；主键发送前再次核验待选状态与游戏前台。取消或失败时仅释放已由助手按下的键。\n'
          '只向目标游戏前台发送，不抢焦点；切出后低频只读以观察换局；开箱/连按/快捷输入期间延后。身份/版本/模式未知时不发送。\n'
          '天赋读取适配build98348，已完成霍格/克罗米试用对照、大厅和镜像归属、克罗米一级自动选中；组合键时序修复后，第2/3层完成输入对照，第4至7层通过完整后台服务连续自动补选并回读确认。乱斗自然升级、连续多局与英文客户端尚待实测，观战/回放未验收。50毫秒是轮询目标，非同帧保证；回读仅确认该层已选择，不独立核对爱心目标。\n'
          '本轮连按测试只使用合成按键、FakeWin32与屏幕外Tk，未向真实游戏发按键；动画效果、游戏接受输入、实际节奏及远程操作仍待手动验收。\n'
          '开箱助手：点击“准备开箱”，手动选好游戏主菜单补给箱页，回游戏单独Space开始→Space暂停→Space继续；Esc在准备阶段也可结束。\n'
          '准备时可切回游戏并选页；30秒未开始准备失效。长按空格只切换一次，助手自己模拟的空格不切换状态。\n'
          '首次Space后，其他按键/鼠标按钮/滚轮或切出目标游戏窗口立即结束本次模式，不自动恢复，需重新点准备。\n'
          '默认每900毫秒最多发送一组空格按下/抬起；每次发送前核验前台为HeroesOfTheStorm_x64.exe且同一HWND/PID。\n'
          '开箱默认关闭，不保存准备状态；仅点击准备后建立原生键鼠钩子，结束即释放。\n'
          '待开始/开箱中/暂停时显示柔雾紫小状态条，穿透且不激活；期间聊天字幕暂藏，结束只恢复未到期内容，不续期。\n'
          '开箱页隐藏中文回复区，草稿和消息历史仍保留。只循环Space，不购买、不重掷，不识别补给箱页、剩余箱数或统计开箱数，用完需手动停止。\n'
          '开箱离线验证仅使用FakeWin32和屏幕外Tk；真实游戏对SendInput的接受、900毫秒动画节奏、Mac远程空格及切窗体验仍待实机验收。\n'
          '采集启动一次即可持续运行：大厅与对局分别自动连接，换局、返回大厅无需手动重新连接；退出游戏后等待重开。\n'
          '显示历史来源只用团队/所有玩家消息校准；队伍、密语与已验证征召团队由独立原生大厅来源接收。旧树持续更新也会按有界计划探测新局。\n'
          '首次未找到聊天时保持有效只读连接，分段自动定位。大厅按本次采集启动时间补收新队伍/密语，接入期间发出的首句也会接收；启动前历史不回放。\n'
          '试用与普通对局首次接入或发现未知记录链时，有限补收该链最近5条支持消息，可能包含启动前近几条；超过5条的首次突发消息可能遗漏。已有游标重连按未读记录接续，不受5条限制。\n'
          '偏好设置→连接→重新连接可立即重新尝试；真正的进程读取错误会释放句柄并自动退避重试。\n'
          '默认接收全部聊天：团队、征召团队、队伍、密语、所有玩家；全部聊天/团队与所有玩家/仅团队都包含普通团队与征召团队，仅征召团队只接收已验证频道的征召聊天；队伍与密语独立，不归入仅团队。实时消息列表按筛选显示，筛选也控制后续接收；不会补收被过滤期间的消息，不删除已有消息。\n'
          '大厅与对局中游戏内可见的队伍/密语使用持续记录接续；征召团队核对当前房间模式及频道类别，不把单一房间属性当作瞬时选人状态；团队/所有玩家在对局中接收，不读取独立战网客户端。\n'
          '同一条记录不会重复翻译，重复发送相同正文仍按不同消息接收；昵称暂缺不阻塞正文，已确认本人发出的密语显示“我 → 联系人未知”，未确认身份仍显示“发送者未知”。\n'
          '大厅队伍姓名在身份确认后，好友关系暂不可读或子记录缺少名字时，尝试同账号有效昵称；身份不明或名字损坏仍显示未知，不借用别人的名字。旧未知消息不自动补名，后续新消息重新查名。\n'
          '返回大厅时会检查旧记录之外的新来源，各来源保留各自游标；旧聊天持续活跃也不会阻止新来源发现。未知远处地址仍需有界分段扫描，不承诺固定接入秒数。\n'
          '接收翻译仅支持英文和韩文：英文按英语、含韩字按韩语译中；含其他语言字母的消息仅保留原文，不自动翻译。显式重试采用相同规则，中文快捷输入仍输出英语或韩语。\n'
          '系统提示与Ping不翻译。当前已完成大厅记录与昵称方向的只读核对，完整换局和新版实际云翻译仍待用户手动验收。\n'
          '实时消息不再逐条显示“阿里云”“缓存”来源标签，原文、译文和错误/防护提示保留；翻译及缓存功能照常工作。\n'
          '标准/精简布局随窗口调整内容排列与间距，消息和输入文字使用保存的内容字号。\n'
          '旧配色统一迁移为柔雾紫，已有偏好和快捷键继续保存在原用户目录。\n'
          '默认透明字幕：11磅/420逻辑像素宽/首次接收起6秒，白字暗边，仅最新1条最多2行；开原文时译文原文各1行。\n'
          '背景真正透明，字幕文字保持不透明；可选半透明消息面板显示最新2条共4行，不透明度仅影响半透明面板（含其调整态）。\n'
          '字幕移到主屏：偏好设置→游戏内→字幕显示器→主显示器→移动到所选屏幕；字幕移到所选屏幕底部并保存，也可选择其他显示器。保存并调整位置/保存并放到底部采用所选屏，同屏调整保留已有自定义位置。底部居中按该屏工作区高度86%估算，不检测HUD，需用户拖动校准。\n'
          '进入字幕调整态后可跨屏拖动，松开鼠标即保存位置与屏幕，完成后点完成调整。所选屏幕拔出或布局变化时回退最近的可用工作区，可重新选屏；聊天字幕选屏不改变Ctrl+T输入条的独立定位。\n'
          '旧chat预设仅一次迁到bottom；自定义位置及非默认字号/停留时间保留，旧默认14磅/440宽/15秒迁为11磅/420宽/6秒。\n'
          'Ctrl+T默认独立底部居中，custom可沿用自定位置；打开暂藏字幕，关闭仅恢复未到期内容，复制成功850毫秒后收起。\n'
          '仍只复制译文，由用户回游戏粘贴发送；完整内容在主窗查看。\n'
          '刷屏防护保留原文，浮窗重复折叠。字幕按所选Windows工作区与DPI布局，原生DPI保持文字清晰。\n'
          '1K/2K/4K与100%–200%缩放已有隔离模拟验证，选屏、跨屏保存与拔屏回退已有合成屏幕及隔离窗口验证；物理双屏、混合DPI及2K/4K实际显示仍待实机验收。\n'
          '换局保留最近500条运行期历史，退出程序释放。\n'
          '支持中文客户端98348；游戏更新后需重新验证采集。\n'
          'build98348支持0–15全部模型解码分支，逐支核对计算代码指纹；2/5/8/11/14还采集并复核运行时解码参数。全部算法已通过独立原生指令对照和合成读取测试；全部分支的朋友机器实机验收仍需持续验证，未知分支或游戏更新后继续停止辅助动作。\n'
          f'显示版本{DISPLAY_VERSION}，程序内版本{VERSION}，发行通道{RELEASE_CHANNEL}；游戏遮挡/输入法候选窗/远程焦点、实际分辨率切换与分场景性能仍待实机验收。\n')
    (bundle/'使用说明.txt').write_text(note,encoding='utf-8-sig')
    stage_demo_launcher(bundle)
    guide=ROOT/'artifacts'/'风暴英雄助手新手指南.html'
    if guide.is_file():shutil.copy2(guide,bundle/guide.name)
    report=work/'安装自检.json'
    started=time.perf_counter()
    subprocess.run([str(bundle/(NAME+'.exe')),'--check-installation',str(report)],check=True,timeout=60,
                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    checked=json.loads(report.read_text(encoding='utf-8'))
    if not checked.get('passed'):raise ValueError('发行包安装自检失败。')
    desktop_report=work/'桌面安装自检.json'
    subprocess.run([str(bundle/(NAME+'.exe')),'--check-desktop-installation',str(desktop_report)],
                   check=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    desktop_checked=json.loads(desktop_report.read_text(encoding='utf-8'))
    if not desktop_checked.get('passed'):raise ValueError('发行包桌面资源/WebView2安装自检失败。')
    rendering_report=work/'桌面渲染自检.json'
    subprocess.run([str(bundle/(NAME+'.exe')),'--check-desktop-rendering',str(rendering_report)],
                   check=True,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    rendering_checked=json.loads(rendering_report.read_text(encoding='utf-8'))
    if not rendering_checked.get('passed'):raise ValueError('发行包WebView2实际渲染/网页桥接自检失败。')
    files=verify_bundle(bundle)
    manifest={'version':VERSION,'channel':RELEASE_CHANNEL,'display_version':DISPLAY_VERSION,
              'files':files,'installation_check':checked,
              'desktop_installation_check':desktop_checked,
              'desktop_rendering_check':rendering_checked,
              'installation_check_seconds':round(time.perf_counter()-started,3)}
    (work/'发行校验.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    target=args.output/release_archive_name()
    # Verify staged output before replacing the package or removing any old products.
    temporary=target.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for row in files:archive.write(bundle/row['path'],NAME+'/'+row['path'])
    verify_archive(temporary,files)
    manifest_target=args.output/'发行校验.json'
    manifest_temporary=manifest_target.with_suffix('.json.tmp')
    manifest_temporary.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    if json.loads(manifest_temporary.read_text(encoding='utf-8'))!=manifest:
        raise ValueError('发行校验文件写入失败。')
    os.replace(temporary,target)
    os.replace(manifest_temporary,manifest_target)
    cleanup_previous_releases(args.output,(target,manifest_target,work))
    print(json.dumps({'zip':str(target),'bytes':target.stat().st_size,'files':len(files),
                      'version':VERSION,'channel':RELEASE_CHANNEL,'display_version':DISPLAY_VERSION,
                      'self_check':checked,'desktop_self_check':desktop_checked,
                      'desktop_rendering_self_check':rendering_checked},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
