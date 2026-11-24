"""记忆巩固：补字段、算权重、标记过期、输出权重表。

用法:
  python consolidate.py            # 跑一次，打印统计
  python consolidate.py --dry      # 只报告不改文件
"""
import os, re, json, sys, time
from datetime import datetime, timedelta
from pathlib import Path
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from config import cfg, expand   # noqa: E402


BASE = Path(expand(cfg("memory.dir", "~/.dsh/.engramory-memory")))
WEIGHTS = Path(expand(cfg("memory.weights", "~/.dsh/mem-tools/weights.json")))
EXCLUDE_DIRS = {"archive"}

# 按类型给默认重要度
TYPE_IMPORTANCE = {
    "feedback": 9,   # 她的规矩，最重要
    "user": 9,
    "self": 8,
    "moment": 7,
    "upcoming": 8,
    "project": 5,
    "reference": 5,
}
# 半衰期（天）
HALF_LIFE = {"feedback": 365, "user": 365, "self": 365, "moment": 90,
             "upcoming": 60, "project": 45, "reference": 120}

FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def parse(path: Path):
    txt = path.read_text(encoding="utf-8", errors="replace")
    m = FM.match(txt)
    fm = {}
    body = txt
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip()
        body = txt[m.end():]
    return fm, body, txt


def main():
    dry = "--dry" in sys.argv
    files = [p for p in BASE.rglob("*.md")
             if not any(part in EXCLUDE_DIRS for part in p.parts)
             and p.name not in ("MEMORY.md", "NOTES-技术.md")]
    # 引用次数（谁链接了谁）
    ref_count = {}
    for p in files:
        _, _, txt = parse(p)
        for ln in re.findall(r"\]\(([^)]+\.md)\)", txt):
            ref_count[os.path.basename(ln)] = ref_count.get(os.path.basename(ln), 0) + 1

    now = time.time()
    table = {}
    filled = 0
    stale = []
    for p in files:
        fm, body, _ = parse(p)
        typ = fm.get("type", "reference")
        imp = fm.get("importance")
        conf = fm.get("confidence")
        if imp is None or conf is None:
            filled += 1
            imp = imp or TYPE_IMPORTANCE.get(typ, 5)
            conf = conf or ("1.0" if "主人" in body[:200] or typ == "feedback" else 0.8)
            if not dry:
                new_fm = dict(fm)
                new_fm.setdefault("importance", str(imp))
                new_fm.setdefault("confidence", str(conf))
                order = ["name", "description", "type", "scope", "importance", "confidence", "created", "updated"]
                lines = ["---"]
                for k in order:
                    if k in new_fm:
                        lines.append(f"{k}: {new_fm[k]}")
                for k, v in new_fm.items():
                    if k not in order:
                        lines.append(f"{k}: {v}")
                lines.append("---")
                fm_text = "\n".join(lines)
                p.write_text(fm_text + "\n" + body, encoding="utf-8")
        # 权重：重要度 × 引用加成 × 时间衰减
        age_days = (now - p.stat().st_mtime) / 86400
        hl = HALF_LIFE.get(typ, 90)
        decay = 0.5 ** (age_days / hl)
        refs = ref_count.get(p.name, 0)
        weight = float(imp) * (1 + 0.18 * refs) * decay
        if refs == 0 and age_days > 30:
            stale.append(p.name)
        table[p.name] = {
            "type": typ,
            "importance": float(imp),
            "confidence": float(conf),
            "refs": refs,
            "age_days": round(age_days, 1),
            "weight": round(weight, 3),
        }

    if not dry:
        WEIGHTS.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"笔记 {len(files)} 篇")
    print(f"补字段 {filled} 篇（importance/confidence）")
    print(f"30 天没被引用、可考虑降级的: {len(stale)} 篇")
    if stale:
        for s in stale[:8]:
            print("   -", s)
    top = sorted(table.items(), key=lambda x: -x[1]["weight"])[:5]
    print("权重最高的 5 条:")
    for k, v in top:
        print(f"   {v['weight']:6.2f}  {k}  (imp={v['importance']}, refs={v['refs']}, {v['type']})")
    print(f"权重表写到 {WEIGHTS}" + ("（dry 模式未写）" if dry else ""))


if __name__ == "__main__":
    main()
