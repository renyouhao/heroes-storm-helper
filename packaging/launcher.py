if __name__=='__main__':
    from multiprocessing import freeze_support
    freeze_support()
    # 冻结子进程先由 multiprocessing 接管，主进程再准备 CLR 并保护应用导入。
    from hots_chat.startup import run
    raise SystemExit(run())
