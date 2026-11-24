# -*- coding: utf-8 -*-
"""entity.py —— 实体抽取与跨会话链接

解决什么问题：语义检索能找"像的问题"，但跟不住"谁、在哪、什么话题"这条线。
你换个说法问同一个人，向量可能就找不到了。

做法：
  1. 从记忆正文和事件流里抽已知实体（人名、组名、地点、猫、项目名）
  2. 建倒排索引：实体 → 出现在哪些记忆/事件
  3. 查询时先按实体命中，再交给语义检索补充

用法:
  python entity.py build              重建索引
  python entity.py query "组B"      查某个实体
  python entity.py list               看实体表
  python entity.py selftest           自测
"""
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
# 原来这里是 `Path(r"C:\\Users\\<名字>\\...")`，别人 clone 下来一个都跑不了。
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from config import cfg, expand   # noqa: E402


MEM = expand(cfg("memory.dir", "~/.dsh/.engramory-memory"))
EV = expand(cfg("runtime.events", "~/.dsh/runtime/events.jsonl"))
OUT = expand(cfg("memory.entity_index", "~/.dsh/mem-tools/entity-index.json"))
CONF = expand(cfg("memory.entities", "~/.dsh/mem-tools/entities.json"))

# ═══════════ 实体表（可扩）═══════════
# （她的人名、19 个组名、显卡型号）。公开仓库不该带这些，
# 改成通用示例 —— 你按自己的情况往里加，跑一次 `python entity.py build` 会从记忆库里自动抽。
DEFAULT_ENTITIES = {
    "人物": ["我", "助手"],
    "地点": ["本机", "家里", "公司", "外面"],
    "项目": ["聊天", "记忆", "写作", "工作"],
    "硬件": ["CPU", "GPU", "内存", "硬盘"],
}


def load_entities():
    if CONF.exists():
        return json.loads(CONF.read_text(encoding="utf-8"))
    CONF.write_text(json.dumps(DEFAULT_ENTITIES, ensure_ascii=False, indent=1), encoding="utf-8")
    return DEFAULT_ENTITIES


def flatten(ents):
    out = []
    for group, names in ents.items():
        for n in names:
            out.append((group, n))
    # 长名优先匹配，避免"组D"被"组"截断
    out.sort(key=lambda x: -len(x[1]))
    return out


def extract(text, pairs):
    """返回命中的实体 [(group, name)]，同名的只留一次"""
    hit = []
    seen = set()
    for group, name in pairs:
        if name in seen:
            continue
        if name in text:
            hit.append((group, name))
            seen.add(name)
    return hit


def build():
    ents = load_entities()
    pairs = flatten(ents)
    idx = defaultdict(lambda: {"memory": [], "event": []})

    # 扫记忆
    n_mem = 0
    for p in sorted(MEM.glob("*.md")):
        if p.name in ("MEMORY.md", "NOTES-技术.md"):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        n_mem += 1
        for group, name in extract(t, pairs):
            key = f"{group}:{name}"
            if p.name not in idx[key]["memory"]:
                idx[key]["memory"].append(p.name)

    # 扫事件流
    n_ev = 0
    if EV.exists():
        for ln in EV.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(ln)
            except json.JSONDecodeError:
                continue
            n_ev += 1
            blob = " ".join(str(e.get(k, "")) for k in ("actor", "target", "summary", "ref"))
            for group, name in extract(blob, pairs):
                key = f"{group}:{name}"
                if len(idx[key]["event"]) < 200:       # 每个实体最多存 200 条事件
                    idx[key]["event"].append({"ts": e.get("ts", "")[:16], "kind": e.get("kind"),
                                              "summary": (e.get("summary") or "")[:60]})

    data = {"built_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
            "n_mem": n_mem, "n_ev": n_ev, "entities": dict(sorted(idx.items()))}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"索引完成：{len(idx)} 个实体，扫了 {n_mem} 条记忆 / {n_ev} 条事件 → {OUT.name}")
    return data


def query(name, limit=10):
    if not OUT.exists():
        print("还没建索引，先跑 build")
        return
    d = json.loads(OUT.read_text(encoding="utf-8"))
    ents = d["entities"]
    hits = {k: v for k, v in ents.items() if k.split(":", 1)[1] == name}
    if not hits:
        # 模糊：包含
        hits = {k: v for k, v in ents.items() if name in k}
    if not hits:
        print(f"没找到实体「{name}」")
        return
    for k, v in hits.items():
        print(f"\n=== {k} ===")
        if v["memory"]:
            print(f"  记忆 {len(v['memory'])} 条: " + ", ".join(v["memory"][:limit]))
        if v["event"]:
            print(f"  事件 {len(v['event'])} 条（最近 {min(limit, len(v['event']))}）:")
            for e in v["event"][-limit:]:
                print(f"    {e['ts']} [{e['kind']}] {e['summary']}")


def listing():
    if not OUT.exists():
        print("还没建索引")
        return
    d = json.loads(OUT.read_text(encoding="utf-8"))
    rows = sorted(d["entities"].items(),
                  key=lambda kv: -(len(kv[1]["memory"]) + len(kv[1]["event"])))
    print(f"共建于 {d['built_at']}，{len(rows)} 个实体\n")
    print(f"{'实体':22s} {'记忆':>4s} {'事件':>5s}")
    for k, v in rows[:30]:
        print(f"{k:22s} {len(v['memory']):4d} {len(v['event']):5d}")


def selftest():
    print("== entity.py 自测 ==")
    ents = load_entities()
    pairs = flatten(ents)
    # 1. 基本抽取
    h = extract("组B和组A在群里讨论了 H3 模型", pairs)
    names = [n for _, n in h]
    assert "组B" in names and "组A" in names and "H3" in names, f"抽取不全: {names}"
    print(f"  抽取 ok：{names}")
    # 2. 长名优先（组D 不被 组 干扰，且不重复）
    h2 = extract("组X息", pairs)
    assert len(h2) == 1 or all(x[1] != "组" for x in h2), f"重复命中: {h2}"
    print(f"  去重 ok：{[n for _, n in h2]}")
    # 3. 空文本不崩
    assert extract("", pairs) == []
    print("  空文本 ok")
    # 4. 不存在的词不误报
    assert extract("量子色动力学", pairs) == []
    print("  无实体不误报 ok")
    print("== 全部通过 ==")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build()
    elif cmd == "query":
        query(sys.argv[2] if len(sys.argv) > 2 else "组B")
    elif cmd == "list":
        listing()
    elif cmd == "selftest":
        selftest()
    else:
        print(__doc__)
