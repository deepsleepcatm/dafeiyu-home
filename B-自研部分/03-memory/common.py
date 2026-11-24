# -*- coding: utf-8 -*-
"""common.py —— 共用基础设施：文件锁、原子写、重试、结构化日志

为什么单独抽出来：这些是所有脚本都要用的底层保证，散在各处必然不一致。
"""
import errno
import json
import os
import sys
import time
import msvcrt
from contextlib import contextmanager
from pathlib import Path
# 原来这里是 `Path(r"C:\\Users\\<名字>\\...")`，别人 clone 下来一个都跑不了。
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from config import cfg, expand   # noqa: E402



# ═══════════ 文件锁（Windows 原生） ═══════════
@contextmanager
def file_lock(lock_path, timeout=10.0, poll=0.05):
    """独占文件锁。跨进程有效，Windows 用 msvcrt.locking。

    用法:
        with file_lock(path.with_suffix(".lock")):
            ...          # 临界区

    超时抛 TimeoutError，不静默继续（静默继续等于没锁）。
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+b")
    deadline = time.time() + timeout
    acquired = False
    try:
        while True:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
                break
            except OSError as e:
                if e.errno not in (errno.EACCES, errno.EDEADLK, 36):
                    raise
                if time.time() > deadline:
                    raise TimeoutError(f"拿不到锁 {lock_path}（{timeout}s）")
                time.sleep(poll)
        yield
    finally:
        if acquired:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        fh.close()


# ═══════════ 原子写 ═══════════
def atomic_write(path, text, encoding="utf-8"):
    """先写临时文件再 replace，读者要么见旧版要么见新版，不会读到半个文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    try:
        with open(tmp, "w", encoding=encoding, newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_append_jsonl(path, obj, lock=True, timeout=10.0):
    """向 JSONL 追加一行。加锁 + 单次 write，保证不丢行、不串行。

    返回 True 表示写入成功；异常向上抛（不吞）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    ctx = file_lock(path.with_suffix(path.suffix + ".lock"), timeout=timeout) if lock else _nullctx()
    with ctx:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    return True


@contextmanager
def _nullctx():
    yield


# ═══════════ 重试 ═══════════
def retry(fn, times=3, delay=0.5, backoff=2.0, on_error=None):
    """重试。最后一次仍失败就抛，不吞。

    on_error(attempt, exc) 在每次失败后调用，用于记日志。
    """
    last = None
    d = delay
    for i in range(1, times + 1):
        try:
            return fn()
        except Exception as e:
            last = e
            if on_error:
                try:
                    on_error(i, e)
                except Exception:
                    pass
            if i < times:
                time.sleep(d)
                d *= backoff
    raise last


# ═══════════ 结构化日志 ═══════════
def log_line(file_path, level, msg, **extra):
    """一行一条 JSON 的日志，方便 grep 和机器读。失败不抛（日志不能成为新的故障源）。"""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "msg": msg}
    if extra:
        rec.update(extra)
    try:
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ═══════════ 自测 ═══════════
def _selftest():
    import tempfile
    import threading
    print("== common.py 自测 ==")
    tmpdir = Path(tempfile.mkdtemp())

    # 1. 原子写
    p = tmpdir / "a.txt"
    atomic_write(p, "hello")
    assert p.read_text(encoding="utf-8") == "hello", "原子写失败"
    print("  原子写 ok")

    # 2. 并发追加 100 行，检查不丢
    jp = tmpdir / "e.jsonl"
    def writer(k):
        for i in range(20):
            atomic_append_jsonl(jp, {"w": k, "i": i})
    ts = [threading.Thread(target=writer, args=(k,)) for k in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    lines = jp.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 100, f"并发追加丢行：期望 100 实得 {len(lines)}"
    print(f"  并发追加 100 行 ok（5 线程 × 20）")

    # 3. 锁超时
    lk = tmpdir / "x.lock"
    got = []
    with file_lock(lk):
        t = threading.Thread(target=lambda: got.append(_try_lock(lk)))
        t.start(); t.join()
    assert got == [False], "锁没有阻止第二个持有者"
    print("  锁互斥 ok")

    # 4. 重试
    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("boom")
        return "ok"
    assert retry(flaky, times=3, delay=0.01) == "ok"
    print("  重试 ok")

    # 5. 重试耗尽要抛
    try:
        retry(lambda: (_ for _ in ()).throw(RuntimeError("always")), times=2, delay=0.01)
        raise AssertionError("重试耗尽没有抛异常")
    except RuntimeError:
        print("  重试耗尽抛异常 ok")

    print("== 全部通过 ==")


def _try_lock(p):
    try:
        with file_lock(p, timeout=0.2):
            return True
    except TimeoutError:
        return False


if __name__ == "__main__":
    _selftest()
