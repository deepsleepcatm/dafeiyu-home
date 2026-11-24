# -*- coding: utf-8 -*-
"""candidate.py —— 候审机制（新经验先隔离，验证通过才转正）

要解决什么：新学到的规则直接写进正式库，等于没验证就上线。
一条规则可能是「一次偶然成功的过度概括」，直接生效会污染判断。

做法：三道状态
  pending   刚提出来，待验证（默认 30 天观察期）
  active    已转正，正式生效
  rejected  已驳回，保留理由（防重复提）

配套：规则账本（每条带来源、命中次数、最后命中时间）

用法:
  python candidate.py add "规则内容" --source "哪次失败" --hypothesis "改了会怎样"
  python candidate.py list [pending|active|rejected|all]
  python candidate.py hit <id>           记录一次命中
  python candidate.py promote <id>       转正（要求命中≥3 或观察期已满且有正证据）
  python candidate.py reject <id> "理由"  驳回
  python candidate.py audit              检查超期未决、长期零命中的
  python candidate.py selftest           自测
"""
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
# 原来这里是 `Path(r"C:\\Users\\<名字>\\...")`，别人 clone 下来一个都跑不了。
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from config import cfg, expand   # noqa: E402


STORE = expand(cfg("intent.candidate", "~/dafeiyu/意图/候审.json"))
OBSERVE_DAYS = 30       # 默认观察期
MIN_HITS = 3            # 转正所需最少命中次数
STALE_DAYS = 90         # 多久没命中算失效候选


def load():
    if STORE.exists():
        return json.loads(STORE.read_text(encoding="utf-8"))
    return {"next_id": 1, "items": []}


def save(d):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def today():
    return datetime.now().strftime("%Y-%m-%d")


def add(rule, source="", hypothesis="", days=OBSERVE_DAYS):
    d = load()
    rid = d["next_id"]
    d["next_id"] = rid + 1
    item = {
        "id": rid,
        "rule": rule.strip(),
        "source": source.strip() or "（未填来源，按不合格处理）",
        "hypothesis": hypothesis.strip() or "（未填假设，无法验证）",
        "state": "pending",
        "created": today(),
        "observe_until": (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d"),
        "hits": 0,
        "last_hit": None,
        "verdict": None,
        "verdict_at": None,
    }
    d["items"].append(item)
    save(d)
    if not source.strip() or not hypothesis.strip():
        print(f"#{rid} 已入候审，但来源或假设为空 —— 这条不能转正，先补全")
    else:
        print(f"#{rid} 已入候审，观察至 {item['observe_until']}")
    return rid


def listing(state="pending"):
    d = load()
    items = [i for i in d["items"] if state == "all" or i["state"] == state]
    if not items:
        print(f"（{state} 下没有条目）")
        return
    print(f"{'#':>3} {'状态':8s} {'命中':>3s} {'观察至':11s} 内容")
    for i in items:
        print(f"{i['id']:3d} {i['state']:8s} {i['hits']:3d} {i['observe_until']:11s} {i['rule'][:46]}")


def hit(rid):
    d = load()
    for i in d["items"]:
        if i["id"] == rid:
            if i["state"] == "rejected":
                print(f"#{rid} 已驳回，不再计命中")
                return
            i["hits"] += 1
            i["last_hit"] = today()
            save(d)
            print(f"#{rid} 命中 {i['hits']} 次")
            return
    print(f"没有 #{rid}")


def promote(rid, force=False):
    d = load()
    for i in d["items"]:
        if i["id"] != rid:
            continue
        if i["state"] != "pending":
            print(f"#{rid} 状态是 {i['state']}，不能转正")
            return
        enough = i["hits"] >= MIN_HITS
        expired = today() >= i["observe_until"]
        if not (enough or (expired and i["hits"] > 0) or force):
            print(f"#{rid} 不能转正：命中 {i['hits']}（需 ≥{MIN_HITS}），"
                  f"观察期到 {i['observe_until']}")
            return
        i["state"] = "active"
        i["verdict"] = f"命中 {i['hits']} 次，转正"
        i["verdict_at"] = today()
        save(d)
        print(f"#{rid} 已转正（命中 {i['hits']} 次）")
        return
    print(f"没有 #{rid}")


def reject(rid, reason):
    d = load()
    for i in d["items"]:
        if i["id"] == rid:
            i["state"] = "rejected"
            i["verdict"] = reason
            i["verdict_at"] = today()
            save(d)
            print(f"#{rid} 已驳回：{reason}")
            return
    print(f"没有 #{rid}")


def audit():
    d = load()
    today_s = today()
    stale_cut = (datetime.now() - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d")
    problems = []
    for i in d["items"]:
        if i["state"] == "pending" and today_s > i["observe_until"]:
            problems.append(f"#{i['id']} 观察期已过（{i['observe_until']}）仍未决：{i['rule'][:40]}")
        if i["state"] == "active" and i["last_hit"] and i["last_hit"] < stale_cut:
            problems.append(f"#{i['id']} 已转正但 {STALE_DAYS} 天零命中，考虑降级：{i['rule'][:40]}")
        if i["state"] == "active" and not i["last_hit"]:
            problems.append(f"#{i['id']} 已转正但从未命中过：{i['rule'][:40]}")
    if not problems:
        print("候审库干净，没有超期或零命中的。")
    else:
        print(f"发现 {len(problems)} 个问题：")
        for p in problems:
            print("  " + p)
    return len(problems)


def selftest():
    """用临时文件跑一遍全流程"""
    import tempfile
    global STORE
    orig = STORE
    STORE = Path(tempfile.mkdtemp()) / "候选.json"
    print("== candidate.py 自测 ==")
    try:
        r1 = add("配置改动后必须读回校验", "故障A", "能拦住写对但不生效")
        assert STORE.exists(), "文件没建"
        print("  add ok")
        hit(r1); hit(r1)
        d = load()
        assert d["items"][0]["hits"] == 2
        print("  hit 计数 ok")
        promote(r1)   # 只有 2 次，不该转正
        assert load()["items"][0]["state"] == "pending", "不该转正却转正了"
        print("  命中不足拒绝转正 ok")
        hit(r1)
        promote(r1)   # 3 次，可以
        assert load()["items"][0]["state"] == "active", "该转正没转"
        print("  满 3 次转正 ok")
        r2 = add("可以删别人的会话", "", "")
        assert len(load()["items"]) == 2
        print("  缺来源/假设仍可入候选 ok（但会提示不能转正）")
        reject(r2, "越权")
        assert load()["items"][1]["state"] == "rejected"
        print("  驳回 ok")
        hit(r2)
        assert load()["items"][1]["hits"] == 0, "已驳回的不该计命中"
        print("  驳回后不再计命中 ok")
    finally:
        STORE = orig
    print("== 全部通过 ==")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit(0)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "add":
        src = ""
        hyp = ""
        for i, a in enumerate(sys.argv):
            if a == "--source":
                src = sys.argv[i + 1]
            if a == "--hypothesis":
                hyp = sys.argv[i + 1]
        add(sys.argv[2], src, hyp)
    elif cmd == "list":
        listing(sys.argv[2] if len(sys.argv) > 2 else "pending")
    elif cmd == "hit":
        hit(int(sys.argv[2]))
    elif cmd == "promote":
        promote(int(sys.argv[2]))
    elif cmd == "reject":
        reject(int(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "（未填理由）")
    elif cmd == "audit":
        sys.exit(1 if audit() else 0)
    else:
        print(__doc__)
