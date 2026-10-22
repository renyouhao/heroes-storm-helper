"""一条自造短句联通测试，默认不联网。"""
from .evaluate_samples import main

if __name__=='__main__':
    import sys
    raise SystemExit(main(['--count','1',*sys.argv[1:]]))
