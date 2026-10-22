"""实机只读回归：用户手动发标记，不点击、不按键、不上传聊天。"""
import argparse
import json
from pathlib import Path
import threading
import time
from hots_chat.acquisition.source import ChatSource
from hots_chat.instance import SingleInstance

MARKER='HOTS_HELPER_TEST_A hello 안녕하세요 你好'


class LiveCollector:
    def __init__(self):self.count=0;self.generations=set();self.gaps=0;self.ready=0;self.lock=threading.Lock()
    def emit(self,event):
        with self.lock:
            if event.kind=='chat' and event.payload.text==MARKER:self.count+=1
            elif event.kind=='generation':self.generations.add(event.generation)
            elif event.kind=='gap':self.gaps+=1
            elif event.kind=='status' and str(event.payload).startswith('已连接'):
                self.ready+=1;print('READY：现在可手动发送测试标记两次。',flush=True)
    def report(self):
        with self.lock:
            return dict(marker_count=self.count,expected_count=2,repeated_multilingual_pass=self.count==2,
                        connection_generations=len(self.generations),ready_count=self.ready,gaps=self.gaps,
                        cloud_calls=0,scope='仅新团队／所有玩家标记；不验证实际译文或发送功能')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--pid',type=int);p.add_argument('--seconds',type=int,default=120)
    p.add_argument('--output',type=Path,default=Path('artifacts/验收报告/实机采集.json'));args=p.parse_args()
    if not 1<=args.seconds<=3600:p.error('seconds 为 1–3600')
    mutex=SingleInstance()
    if not mutex.acquire():print('请先关闭助手，再运行只读实机测试。');return 1
    source=ChatSource(selected_pid=args.pid);collector=LiveCollector()
    try:
        print('如当前聊天历史为空，请先手动发一条普通消息用于校准。')
        print('等待 READY 后，在团队频道将以下标记发两次：\n'+MARKER,flush=True)
        source.start(collector.emit);deadline=time.monotonic()+args.seconds
        while time.monotonic()<deadline:time.sleep(min(.5,deadline-time.monotonic()))
    except KeyboardInterrupt:pass
    finally:source.stop();mutex.close()
    report=collector.report();args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=True))
    return 0 if report['repeated_multilingual_pass'] else 2


if __name__=='__main__':raise SystemExit(main())
