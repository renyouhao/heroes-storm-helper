"""默认只统计自造语料；--live 才联网，且与桌面程序共用单实例锁。"""
import argparse
import json
from pathlib import Path
import time
import uuid
from hots_chat.models import TranslationJob
from hots_chat.chat_text import character_count
from hots_chat.credentials import CredentialStore
from hots_chat.paths import AppPaths
from hots_chat.translation.aliyun import AliyunEngine
from hots_chat.translation.base import TranslationError
from hots_chat.instance import SingleInstance

ROOT=Path(__file__).resolve().parents[1]


def evaluate(samples,live=False,engine_factory=None):
    rows=[];calls=0;engine=None
    try:
        for sample in samples:
            text,source,target=sample['text'],sample['source'],sample['target']
            row=dict(characters=character_count(text),source=source,target=target,grade='未联网／待实译',elapsed_ms=None)
            if live:
                if engine_factory is None:raise ValueError('真实评测需要翻译引擎。')
                job=TranslationJob(uuid.uuid4().hex,None,0,text,source,target,'evaluation')
                started=time.perf_counter()
                try:
                    if engine is None:engine=engine_factory()
                    calls+=1;result=engine.translate(job)
                    row.update(translation=result.translated,grade='已实译／待人工语义校对')
                except TranslationError:row['grade']='翻译失败'
                row['elapsed_ms']=round((time.perf_counter()-started)*1000,2)
            rows.append(row)
    finally:
        if engine:engine.close()
    return dict(mode='live' if live else 'dry-run',cloud_calls=calls,items=rows,semantic_quality='待人工核对；离线回归不证明翻译质量')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--live',action='store_true');p.add_argument('--count',type=int,default=30)
    p.add_argument('--output',type=Path,default=ROOT/'artifacts/验收报告/语料评测.json')
    args=p.parse_args(argv)
    if not 1<=args.count<=30:p.error('count 为 1–30')
    samples=json.loads((ROOT/'data/evaluation_samples.json').read_text(encoding='utf-8'))[:args.count]
    mutex=SingleInstance()
    if not mutex.acquire():print('请先关闭桌面助手，再运行评测。');return 1
    try:
        paths=AppPaths.from_root(ROOT)
        saved=CredentialStore(paths.credentials_file).load() if args.live else None
        if args.live and saved is None:
            print('请先在桌面助手配置自己的密钥。');return 2
        result=evaluate(samples,args.live,lambda:AliyunEngine(saved.credentials))
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'评测 {len(samples)} 条，云调用 {result["cloud_calls"]} 次。')
        return 0
    finally:mutex.close()


if __name__=='__main__':raise SystemExit(main())
