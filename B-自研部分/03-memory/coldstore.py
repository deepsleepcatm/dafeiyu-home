# -*- coding: utf-8 -*-
"""coldstore.py —— 频率型遗忘（把长期零命中的挪进冷存，不删）

依据：《Memory in the Age of AI Agents》5.2.3 三种遗忘机制
  - 时间型：只按创建时间衰减（MemGPT 满上下文逐最早）
  - 频率型：按检索行为，保留常访问的（XMem 用 LFU）
  - 重要性型：综合语义价值

论文同时警告："过度激进的剪枝会抹掉罕见但关键的知识。"

所以这里只做「移出检索池」，不做删除；配合 tombstone.py 可回溯。

规则（保守）：
  hits == 0  且  age > 90 天  且  importance < 9  → 移入 _cold/
  其余保持不动

用法:
  python coldstore.py scan             只看哪些会被移走（dry-run）
  python coldstore.py apply            真的移
  python coldstore.py restore <文件名>  移回来
  python coldstore.py selftest
"""
import json
import shutil
import sys
import time
from pathlib import Path
# 原来这里是 `Path(r"C:\\Users\\<名字>\\...")`，别人 clone 下来一个都跑不了。
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from config import cfg, expand   # noqa: E402


MEM = expand(cfg("memory.dir", "~/.dsh/.engramory-memory"))
COLD = MEM / "_cold"
WEIGHTS = expand(cfg("memory.weights", "~/.dsh/mem-tools/weights.json"))

MIN_AGE_DAYS = 90
MAX_IMPORTANCE = 9


def _read_fm(p):
    t = p.read_text(encoding="utf-8", errors="replace")
    if not t.startswith("---"):
        return {}
    end = t.find("\n---", 3)
    fm = {}
    for ln in t[3:end if end > 0 else 0].splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm


def candidates():
    w = json.loads(WEIGHTS.read_text(encoding="utf-8")) if WEIGHTS.exists() else {}
    now = time.time()
    out = []
    for p in sorted(MEM.glob("*.md")):
        if p.name in ("MEMORY.md", "NOTES-技术.md") or p.parent != MEM:
            continue
        rec = w.get(p.name, {})
        hits = int(rec.get("hits", 0) or 0)
        fm = _read_fm(p)
        try:
            imp = float(fm.get("importance", 5))
        except (TypeError, ValueError):
            imp = 5
        age = (now - p.stat().st_mtime) / 86400
        if hits == 0 and age > MIN_AGE_DAYS and imp < MAX_IMPORTANCE:
            out.append({"file": p.name, "hits": hits, "importance": imp, "age_days": round(age, 1)})
    return out


def scan():
    c = candidates()
    if not c:
        print(f"没有符合冷存条件的（判据：hits=0 且 龄>{MIN_AGE_DAYS}天 且 重要度<{MAX_IMPORTANCE}）")
        return c
    print(f"符合冷存条件 {len(c)} 条：")
    print(f"{'文件':40s} {'命中':>4s} {'重要度':>6s} {'龄(天)':>7s}")
    for x in c:
        print(f"{x['file']:40s} {x['hits']:4d} {x['importance']:6.1f} {x['age_days']:7.1f}")
    return c


def apply():
    c = candidates()
    if not c:
        print("没有可移的")
        return 0
    COLD.mkdir(parents=True, exist_ok=True)
    moved = 0
    for x in c:
        src = MEM / x["file"]
        dst = COLD / x["file"]
        try:
            shutil.move(str(src), str(dst))
            moved += 1
        except Exception as e:
            print(f"  {x['file']} 移动失败: {e}")
    print(f"已移入冷存 {moved} 条（原文在 {COLD}，随时可 restore）")
    return moved


def restore(name):
    src = COLD / name if not name.endswith(".md") else COLD / name
    if not src.exists():
        print(f"冷存里没有 {name}")
        return 1
    dst = MEM / src.name
    if dst.exists():
        print(f"库里已有同名 {src.name}，不覆盖")
        return 1
    shutil.move(str(src), str(dst))
    print(f"已恢复 {src.name}")
    return 0


def selftest():
    import tempfile
    global MEM, COLD, WEIGHTS
    orig = (MEM, COLD, WEIGHTS)
    tmp = Path(tempfile.mkdtemp())
    MEM = tmp
    COLD = MEM / "_cold"
    WEIGHTS = MEM / "w.json"
    print("== coldstore.py 自测 ==")
    try:
        # 三条：该移、不该移（高重要度）、不该移（新）
        for name, imp, age in [("old_low.md", 5, 200), ("old_high.md", 9, 200), ("new.md", 5, 1)]:
            p = MEM / name
            p.write_text(f"---\nimportance: {imp}\ntype: reference\n---\n正文\n", encoding="utf-8")
            t = time.time() - age * 86400
            import os
            os.utime(p, (t, t))
        WEIGHTS.write_text(json.dumps({}), encoding="utf-8")
        c = candidates()
        names = [x["file"] for x in c]
        assert names == ["old_low.md"], f"候选错了: {names}"
        print(f"  判据 ok（只有 old_low.md 入候选）")
        assert apply() == 1
        assert (COLD / "old_low.md").exists() and not (MEM / "old_low.md").exists()
        print("  移入冷存 ok")
        assert restore("old_low.md") == 0
        assert (MEM / "old_low.md").exists()
        print("  restore ok")
    finally:
        MEM, COLD, WEIGHTS = orig
    print("== 全部通过 ==")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        scan()
    elif cmd == "apply":
        apply()
    elif cmd == "restore":
        sys.exit(restore(sys.argv[2]))
    elif cmd == "selftest":
        selftest()
    else:
        print(__doc__)
