"""note.py —— 记忆写入器（强制 source 校验，降低"靠自觉"的门槛）

一条命令写入一条记忆，自动带标准 frontmatter、自动更新 MEMORY.md 索引。

用法:
  python note.py --title "作息反了" --type feedback --importance 9 \
      --source 她亲口 --ref "2026-11-20 微信原话" --body "她夜里醒白天睡…"
  python note.py --check    检查：source=我实测 是否有 ref；索引是否漏项
"""
import argparse, os, re, sys, time
from pathlib import Path
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from config import cfg, expand   # noqa: E402


MEM = Path(expand(cfg("memory.dir", "~/.dsh/.engramory-memory")))
INDEX = MEM / "MEMORY.md"
VALID_SOURCE = {"她亲口", "我实测", "外部资料", "我推的"}
SECTION = {"feedback": "反馈", "user": "反馈", "self": "我自己", "moment": "经历",
           "upcoming": "约定", "project": "技术参考与项目（按需读）",
           "reference": "技术参考与项目（按需读）"}


def safe_name(title):
    return re.sub(r'[\\/:*?"<>|]', "_", title).strip()


def write_note(title, typ, importance, source, ref, body, scope="global", stance="unverified", dry=False):
    name = safe_name(title)
    p = MEM / f"{name}.md"
    if p.exists():
        print(f"已存在，未覆盖：{p.name}（要改请手动编辑，避免覆盖）")
        return 1
    now = time.strftime("%Y-%m-%d")
    fm = [
        "---",
        f"name: {name}",
        f"description: {body.strip().splitlines()[0][:60]}",
        f"type: {typ}",
        f"scope: {scope}",
        f"importance: {importance}",
        f"source: {source}",
        f'source_ref: "{ref}"',
        f"stance: {stance}",
        f"confidence: {'1.0' if source == '她亲口' else ('1.0' if source == '我实测' else '0.8')}",
        f"created: {now}",
        f"updated: {now}",
        "---",
    ]
    if dry:
        print("\n".join(fm) + "\n" + body.strip())
        print("（--dry：只打印，未写盘）")
        return 0
    p.write_text("\n".join(fm) + "\n" + body.strip() + "\n", encoding="utf-8")
    print(f"写入：{p.name}")

    # 更新索引
    if INDEX.exists():
        idx = INDEX.read_text(encoding="utf-8")
        sec = SECTION.get(typ, "技术参考与项目（按需读）")
        line = f"- [{name}]({name}.md) — {body.strip().splitlines()[0][:60]}"
        if f"]({name}.md)" in idx:
            print("索引已有该项，跳过")
        elif f"## {sec}" in idx:
            idx = idx.replace(f"## {sec}\n", f"## {sec}\n\n{line}\n", 1)
            INDEX.write_text(idx, encoding="utf-8")
            print(f"索引已更新（{sec}）")
        else:
            idx = idx.rstrip() + f"\n\n## {sec}\n\n{line}\n"
            INDEX.write_text(idx, encoding="utf-8")
            print(f"索引新建小节（{sec}）")
    return 0


def check():
    print("== 检查 1：source=我实测 必须有 ref ==")
    bad = []
    for p in sorted(MEM.glob("*.md")):
        if p.name in ("MEMORY.md", "NOTES-技术.md"):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", t, re.S)
        if not m:
            continue
        fm = m.group(1)
        src = re.search(r"^source:\s*(.+)$", fm, re.M)
        ref = re.search(r"^source_ref:\s*(.+)$", fm, re.M)
        if src and src.group(1).strip() in VALID_SOURCE:
            if src.group(1).strip() == "我实测" and (not ref or not ref.group(1).strip().strip('"')):
                bad.append(p.name)
        else:
            bad.append(p.name + "(source 非法)")
    print("  违规:", bad if bad else "无")

    print("== 检查 2：索引是否漏项（MEMORY.md + NOTES-技术.md 都算）==")
    idx = INDEX.read_text(encoding="utf-8") if INDEX.exists() else ""
    notes = Path(str(INDEX).replace("MEMORY.md", "NOTES-技术.md"))
    idx2 = notes.read_text(encoding="utf-8") if notes.exists() else ""
    missing = []
    for p in sorted(MEM.glob("*.md")):
        if p.name in ("MEMORY.md", "NOTES-技术.md"):
            continue
        if f"]({p.name})" not in idx and f"]({p.name})" not in idx2 and p.name[:-3] not in idx2:
            missing.append(p.name)
    print("  索引漏项:", missing if missing else "无")
    return len(bad) + len(missing)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--title"); ap.add_argument("--type", default="reference")
    ap.add_argument("--importance", type=int, default=5)
    ap.add_argument("--source", default="我推的"); ap.add_argument("--ref", default="")
    ap.add_argument("--body", default=""); ap.add_argument("--scope", default="global")
    ap.add_argument("--stance", default="unverified",
                    choices=["support", "challenge", "mention", "unverified"],
                    help="这条依据对结论是 support 支持 / challenge 质疑 / mention 只是提到 / unverified 未标")
    ap.add_argument("--dry", action="store_true", help="只打印 frontmatter，不写盘")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        sys.exit(1 if check() else 0)
    if not a.title or not a.body:
        print("需要 --title 和 --body")
        sys.exit(1)
    if a.source not in VALID_SOURCE:
        print(f"source 必须是 {VALID_SOURCE} 之一")
        sys.exit(1)
    if a.source == "我实测" and not a.ref.strip():
        print("source=我实测 必须给 --ref（文件+行号 或 命令+输出），写不出就降级成「我推的」")
        sys.exit(1)
    sys.exit(write_note(a.title, a.type, a.importance, a.source, a.ref, a.body, a.scope, a.stance, a.dry))
