"""本机生成免安装目录包；只显式复制公开种子，不读取用户配置。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from hots_chat import APP_NAME,__version__

ROOT=Path(__file__).resolve().parents[1]
NAME=APP_NAME
VERSION=__version__
PUBLIC_SEEDS=('phrases.json','talents_98348_zhCN.json','talent_catalog_source.json')


def stage_public_data(root, destination):
    for name in PUBLIC_SEEDS:
        target=Path(destination)/'data'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(Path(root)/'data'/name,target)


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


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'releases')
    args=parser.parse_args(argv)
    args.output.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='hots-release-',dir=args.output))
    stage_public_data(ROOT,work/'public')
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--windowed','--noupx',
             '--name',NAME,'--paths',str(ROOT/'src'),'--distpath',str(work/'dist'),
             '--workpath',str(work/'build'),'--specpath',str(work)]
    command+=['--manifest',str(ROOT/'packaging'/'highdpi.manifest')]
    for name in PUBLIC_SEEDS:
        command+=['--add-data',str(work/'public'/'data'/name)+os.pathsep+'data']
    command.append(str(ROOT/'packaging'/'launcher.py'))
    subprocess.run(command,cwd=ROOT,check=True)
    bundle=work/'dist'/NAME
    note=(f'{NAME} {VERSION} · Windows x64 免安装测试版\n\n'
          f'解压整个文件夹，双击“{NAME}.exe”，无需安装Python。\n'
          '首次使用先在阿里云开通机器翻译通用版并给RAM用户翻译权限；助手使用AccessKey，不填写阿里云网页登录账号密码。\n'
          '填入自己的AccessKey ID/Secret，点击一次“连接并开始使用”：自动验证身份、测试翻译“你好”，成功后加密保存并启用翻译。\n'
          '无需填写或确认用量；助手不统计用量或限制月度额度，实际用量与费用以阿里云控制台为准。旧用量文件保留但不再读取。\n'
          '处理时字段暂时禁用；取消保留原配置。测试失败或超时后可修改配置并手动重试。\n'
          '失败提示当前阶段、安全错误类别或可识别云错误码；未知错误不直接展示底层内容。可先检查Key配对、网络、服务开通和RAM权限。\n'
          '配置目录自动创建，无需拷贝AppData或另装SDK；首次不必分两步操作。\n'
          '可跳过云配置，只接收原文。密钥仅保存在当前Windows用户的本机加密文件。\n'
          '无边框游戏模式；Ctrl+T中文翻译并复制，再由你粘贴发送。\n'
          '设置→外观可选择月白蓝/石墨紫/雾青；保存后同步主窗、设置、译文和输入条，下次启动记住。\n'
          '主窗宽屏侧边导航提供实时消息/常用短句/开箱助手/动作连按/天赋辅助/双击播报/偏好设置，窄屏保留顶部标签；聊天与回复使用圆角容器。\n'
          '双击播报默认关闭：开启后在游戏Tab天赋页双击一个已选天赋，说明玩家、英雄、真实天赋等级、名称与一句关键效果，发到己方团队；统计页点哪项只说哪项，0可说，破折号不说，不加排名或评价。短效果离线提供，无需翻译Key或AI。\n'
          '保留游戏默认Ctrl+Enter团队快捷键；双击后松开鼠标按键并保持前台。聊天框/菜单或开箱/连按/快捷输入/升级准备期间暂停。先核对团队空框，输入后逐字回读再提交；不改剪贴板或覆盖草稿。失败不自动重试，未提交文字按Esc取消；同项3秒、全局2秒限频，关开不重置。\n'
          '兼容带Windows注入标记的桌面点击，忽略助手自身标记的事件。未识别、取消或限频原因请看助手的“双击播报”页；主窗口底部的大厅/对局文字仅表示聊天采集来源。Tab没关就退出对局也会使旧待发动作失效，新局重新打开面板并双击。\n'
          '本轮修复空闲F8连按监听与双击播报争用输出锁，造成文字缓慢出现或未发送的问题。热键与计时回调不等待其他发送器持有的输出栅栏；连按和播报输出仍互斥，取消仍等待已有输入结束。正文仍为批量输入，不改剪贴板。\n'
          '输入框短暂变化时在同一可信对局内有界重读，正文与发送按键不重复执行；换局、菜单、切出或新操作仍停止。文字留框时查看“双击播报”页的具体原因，先按Esc关掉草稿再双击；提示可能已发送时先查聊天记录。切回助手的普通操作保留上次结果；诊断默认关闭，开启后记录发送阶段/错误码/耗时/字数，不含播报正文。\n'
          '主窗检查更新：手动检查官方稳定Release、下载并校验SHA256、重启并更新。无启动自动联网或降级；独立升级程序备份替换，失败尝试回滚；AppData密钥、短句与偏好保留。源码模式展示ZIP路径供手动使用，不替换源码。实际冻结包跨版本升级仍待验收。\n'
          '动作连按：先选择S或Y+S，填写“按键间隔”并保存应用，回游戏单独F8开始、再F8取消；无需准备。两种动作都可设20–5000毫秒整数，分别保存，新配置均默认20；旧版保存值保留。S为两次S之间的间隔；Y+S为Y→间隔→S→间隔循环；保存配置结束旧动作。\n'
          '启动及运行沿用游戏底部被动状态条，提示“按 F8 取消”；取消后收起，不抢鼠标或输入焦点。\n'
          '设置→快捷键可将连按开关改为F1–F12，提示同步实际按键；保存改键结束本次连按，下次新按即可开始，不保存运行状态。\n'
          '正式模式启动后监听快捷键，空闲不发送S；长按只切换一次。Esc、Enter、组合键、打开Ctrl+T或切出目标游戏窗口都会结束本次连按，回游戏不自动恢复；松开后新按快捷键可再次开始，此前Enter/Esc的状态不阻止下一次启动。\n'
          '连按每次发送所选动作中的一个键并完整抬起；聊天框开着时会输入S或交替输入Y和S。不自动点击鼠标，每次发送前核验真实游戏前台且同一HWND/PID。\n'
          '毫秒为目标发送间隔，实际受Windows调度和游戏响应影响；卡顿不追补，不保证精确次数或提升真实攻击速度。\n'
          '连按与开箱互斥：按F8后先显示启动状态条，关闭开箱发送门并等旧开箱线程退出，才允许发送所选按键；未能退出会取消并提示。活动期间聊天字幕暂藏且不续期。\n'
          '图片表情：正文中的完整img图片标签显示为[表情]，纯表情不调用云翻译，英韩混合消息保留正常翻译。\n'
          '诊断日志默认完全关闭，不启动写线程、不建日志文件。遇到问题时设置→诊断→开启诊断日志→复现→导出ZIP；仅本次运行有效，下次启动仍关闭。开启后后台有界记录关键事件，过载丢弃计数，最多约2MiB×5份、保留7天。\n'
          '详细诊断可主动开启10分钟，正文与译文各最多256字；完整已配置密钥始终脱敏，分享前仍需检查正文隐私。导出不含凭据文件/数据库/偏好、不上传、不调用云接口。\n'
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
          '待开始/开箱中/暂停时显示三主题小状态条，穿透且不激活；期间聊天字幕暂藏，结束只恢复未到期内容，不续期。\n'
          '开箱页隐藏中文回复区，草稿和消息历史仍保留。只循环Space，不购买、不重掷，不识别补给箱页、剩余箱数或统计开箱数，用完需手动停止。\n'
          '开箱离线验证仅使用FakeWin32和屏幕外Tk；真实游戏对SendInput的接受、900毫秒动画节奏、Mac远程空格及切窗体验仍待实机验收。\n'
          '采集启动一次即可持续运行：大厅与对局分别自动连接，换局、返回大厅无需手动重新连接；退出游戏后等待重开。\n'
          '首次未找到聊天时保持有效只读连接，分段自动定位。大厅按本次采集启动时间补收新队伍/密语，接入期间发出的首句也会接收；启动前历史不回放。\n'
          '试用与普通对局首次接入或发现未知记录链时，有限补收该链最近5条支持消息，可能包含启动前近几条；超过5条的首次突发消息可能遗漏。已有游标重连按未读记录接续，不受5条限制。\n'
          '设置→连接→手动重新连接可立即重新尝试；真正的进程读取错误会释放句柄并自动退避重试。\n'
          '默认接收全部聊天：团队、队伍、密语、所有玩家；实时消息频道可选仅队伍或仅密语等筛选，仅影响后续消息，不清除历史。\n'
          '大厅与对局中游戏内可见的队伍/密语使用持续记录接续；团队/所有玩家在对局中接收；不读取独立战网客户端。\n'
          '同一条记录不会重复翻译，重复发送相同正文仍按不同消息接收；昵称暂缺不阻塞正文，已确认本人发出的密语显示“我 → 联系人未知”，未确认身份仍显示“发送者未知”。\n'
          '大厅队伍姓名在身份确认后，好友关系暂不可读或子记录缺少名字时，尝试同账号有效昵称；身份不明或名字损坏仍显示未知，不借用别人的名字。旧未知消息不自动补名，后续新消息重新查名。\n'
          '返回大厅时会检查旧记录之外的新来源，各来源保留各自游标；旧聊天持续活跃也不会阻止新来源发现。未知远处地址仍需有界分段扫描，不承诺固定接入秒数。\n'
          '接收翻译仅支持英文和韩文：英文按英语、含韩字按韩语译中；含其他语言字母的消息仅保留原文，不自动翻译。显式重试采用相同规则，中文快捷输入仍输出英语或韩语。\n'
          '系统提示与Ping不翻译。当前已完成大厅记录与昵称方向的只读核对，完整换局和新版实际云翻译仍待用户手动验收。\n'
          '实时消息不再逐条显示“阿里云”“缓存”来源标签，原文、译文和错误/防护提示保留；翻译及缓存功能照常工作。\n'
          '紧凑模式或大字号时输入与预览并排，最小窗口缩小间距。\n'
          '旧紫晶迁移为石墨紫，旧冰蓝迁移为月白蓝，原有位置与快捷键保留。\n'
          '默认透明字幕：11磅/420逻辑像素宽/首次接收起6秒，白字暗边，仅最新1条最多2行；开原文时译文原文各1行。\n'
          '背景真正透明，字幕文字保持不透明；可选深色半透明面板显示最新2条共4行，不透明度仅影响半透明面板（含其调整态）。\n'
          '设置→游戏内→保存并使用底部字幕布局：底部居中，底边按工作区高度86%估算，不检测HUD，需用户拖动校准。\n'
          '旧chat预设仅一次迁到bottom；自定义位置及非默认字号/停留时间保留，旧默认14磅/440宽/15秒迁为11磅/420宽/6秒。\n'
          'Ctrl+T默认独立底部居中，custom可沿用自定位置；打开暂藏字幕，关闭仅恢复未到期内容，复制成功850毫秒后收起。\n'
          '仍只复制译文，由用户回游戏粘贴发送；完整内容在主窗查看。\n'
          '刷屏防护保留原文，浮窗重复折叠。按Windows工作区与DPI适配单屏，原生DPI保持文字清晰。\n'
          '1K/2K/4K与100%–200%缩放已有隔离模拟验证，2K/4K物理显示仍待验收。\n'
          '本机90万字符/月保护不关闭阿里云后付费；同主账号其它设备新调用需自行核对。\n'
          '换局保留最近500条运行期历史，退出程序释放。\n'
          '支持中文客户端98348；游戏更新后需重新验证采集。\n'
          '当前是本地构建测试版；游戏遮挡/输入法候选窗/远程焦点、实际分辨率切换与分场景性能仍待实机验收。\n')
    (bundle/'使用说明.txt').write_text(note,encoding='utf-8-sig')
    guide=ROOT/'artifacts'/'风暴英雄助手新手指南.html'
    if guide.is_file():shutil.copy2(guide,bundle/guide.name)
    report=work/'安装自检.json'
    started=time.perf_counter()
    subprocess.run([str(bundle/(NAME+'.exe')),'--check-installation',str(report)],check=True,timeout=60,
                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    checked=json.loads(report.read_text(encoding='utf-8'))
    if not checked.get('passed'):raise ValueError('发行包安装自检失败。')
    files=verify_bundle(bundle)
    manifest={'version':VERSION,'files':files,'installation_check':checked,
              'installation_check_seconds':round(time.perf_counter()-started,3)}
    (work/'发行校验.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    target=args.output/(NAME+'-'+VERSION+'-Windows-x64.zip')
    # Write a new archive then atomically replace, never recurse over user's files.
    temporary=target.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for row in files:archive.write(bundle/row['path'],NAME+'/'+row['path'])
    os.replace(temporary,target)
    (args.output/'发行校验.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'zip':str(target),'bytes':target.stat().st_size,'files':len(files),'self_check':checked},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
