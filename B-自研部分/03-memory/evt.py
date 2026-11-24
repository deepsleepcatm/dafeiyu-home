"""evt.py —— 事件流写入器（书 6.3 的实现，多窗口共享总线）

用法:
  python evt.py decision "改了检索公式" --target memory/检索 --ref 论文§4.3
  python evt.py action "补了23篇source" --target memory
  python evt.py failure "frontmatter假值" --target memory --ref E4
  python evt.py handoff "交给组A" --target group
  python evt.py tail 10      看最近 10 条
  python evt.py today        看今天写了几条、有没有空档

四种 kind：decision（做了决定）/ action（做了动作）/ failure（失败）/ handoff（交接）
"""
import argparse, json, os, sys, time
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from common import atomic_append_jsonl
from pathlib import Path
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from config import cfg, expand   # noqa: E402


EV = Path(expand(cfg("runtime.events", "~/.dsh/runtime/events.jsonl")))


def append(kind, summary, target="", ref="", actor="组C", verify_how=""):
    EV.parent.mkdir(parents=True, exist_ok=True)
    e = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
         "actor": actor, "kind": kind, "target": target,
         "summary": summary, "ref": ref,
         # 声明与证据分离：attempted = 做了；verified = 验了（有证据）
         "attempted": True,
         "verified": bool(verify_how.strip()),
         "verify_how": verify_how.strip() or None}
    atomic_append_jsonl(EV, e)          # 加锁 + fsync，并发不丢行
    print(f"已记：{kind} · {summary}")
    return 0


def tail(n=10):
    if not EV.exists():
        print("事件流还不存在")
        return
    lines = EV.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    for ln in lines[-n:]:
        try:
            e = json.loads(ln)
            print(f"{e['ts'][11:16]} [{e['kind']:8s}] {e['summary']}  ({e.get('target','')})")
        except Exception:
            print(ln[:100])


def today():
    if not EV.exists():
        print("事件流还不存在")
        return
    d = time.strftime("%Y-%m-%d")
    n = 0
    for ln in EV.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(ln)
        except Exception:
            continue
        if e["ts"].startswith(d):
            n += 1
    print(f"今天（{d}）事件 {n} 条")
    if n == 0:
        print("⚠ 今天一条事件都没有 —— 要么没干活，要么干了没记")
    return n


def unverified(day=None):
    """没验的动作占比。占位最危险：说了做了但没证据。"""
    if not EV.exists():
        print("事件流还不存在")
        return
    d = day or time.strftime("%Y-%m-%d")
    acts = []
    for ln in EV.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not (e.get("ts") or "").startswith(d):
            continue
        if e.get("kind") == "action":
            acts.append(e)
    if not acts:
        print(f"{d} 没有 action 记录")
        return
    bad = [a for a in acts if not a.get("verified")]
    print(f"{d}：action {len(acts)} 条，未验证 {len(bad)} 条（{len(bad)/len(acts)*100:.0f}%）")
    if bad:
        print("  未验的：")
        for a in bad[-8:]:
            print(f"    {a['ts'][11:16]} {a.get('summary','')[:60]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="tail")
    ap.add_argument("summary", nargs="?", default="")
    ap.add_argument("--target", default=""); ap.add_argument("--ref", default="")
    ap.add_argument("--actor", default="组C"); ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--verified", default="", help="怎么验的（命令输出/读回/渲染结果），不传即未验证")
    a = ap.parse_args()
    if a.cmd == "tail":
        tail(a.n)
    elif a.cmd == "today":
        today()
    elif a.cmd == "unverified":
        unverified(sys.argv[2] if len(sys.argv) > 2 else None)
    elif a.cmd in ("decision", "action", "failure", "handoff"):
        if not a.summary:
            print("需要一句 summary")
            sys.exit(1)
        sys.exit(append(a.cmd, a.summary, a.target, a.ref, a.actor, a.verified))
    else:
        print(__doc__)
