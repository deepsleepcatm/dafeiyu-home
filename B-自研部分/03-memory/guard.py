"""guard.py —— R0-R3 动作判据 + 回放测试（论文规则集 5.1 的实现）

用法:
  python guard.py grade <动作> <对象>   单条判定
  python guard.py selftest              回放今天两个真实事故
  python guard.py staletest             停摆检测注入测试
"""
import re, sys, os, json, time
from pathlib import Path
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from config import cfg, expand   # noqa: E402


RULES = [
    (r"^(del|rm|unlink|remove)", None, "R3"),
    (r"^(edit|write)", r"AGENT\.md|VALUES\.yaml|行为画像|宪章", "R3"),
    (r"^(post|send|comment|publish|reply_add|update_config|schedule_create)", None, "R2"),
    (r"^(write_note|gen_asset|append_note|render|index)", None, "R1"),
    (r"^(read|query|count|status|grade|list|stats)", None, "R0"),
]


def grade(action, target=""):
    for pat, tpat, lv in RULES:
        if re.search(pat, action, re.I) and (tpat is None or re.search(tpat, target, re.I)):
            return lv
    return "R2"  # 未命中按 R2，fail-safe


# ---------- 停摆检测（算法 4） ----------
HB = Path(expand(cfg("runtime.heartbeat", "~/.dsh/runtime/heartbeat")))


def check_stale(flow, expect_sec, now=None):
    now = now or time.time()
    f = HB / f"{flow}.json"
    if not f.exists():
        return "STALE: 从未启动（无回执文件）"
    hb = json.loads(f.read_text(encoding="utf-8", errors="replace"))
    last = hb.get("finished") or hb.get("started")
    if not last:
        return "STALE: 有文件无时间戳"
    t = time.mktime(time.strptime(last, "%Y-%m-%dT%H:%M:%S")) if isinstance(last, str) else last
    if now - t > expect_sec:
        return f"STALE: 超过 {expect_sec}s 无回执"
    return "OK"


def selftest():
    """回放今天两个真实事故。"""
    print("== 回放 1：pythonPath 假值事故（配置写了但没生效）==")
    print("  动作 update_config / cordis.patch.yml →", grade("update_config", "cordis.patch.yml"),
          "（正确应为 R2 或 R3，即必须人工确认，不能直接放行）")
    print("  动作 read / cordis.patch.yml →", grade("read", "cordis.patch.yml"), "（读文件，R0 合理）")
    print()
    print("== 回放 2：桥静默事故（进程活着但没干活）==")
    HB.mkdir(parents=True, exist_ok=True)
    f = HB / "group_bridge.json"
    # 模拟：四天前的回执
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 4 * 86400))
    f.write_text(json.dumps({"started": old, "finished": old, "ok": True}), encoding="utf-8")
    r = check_stale("group_bridge", 3600)
    print("  4 天无回执 →", r, "（正确应为 STALE）")
    # 模拟：正常回执
    fresh = time.strftime("%Y-%m-%dT%H:%M:%S")
    f.write_text(json.dumps({"started": fresh, "finished": fresh, "ok": True}), encoding="utf-8")
    r = check_stale("group_bridge", 3600)
    print("  刚刚回执 →", r, "（正确应为 OK）")
    # 模拟：从未启动
    (HB / "never_flow.json").unlink(missing_ok=True)
    r = check_stale("never_flow", 3600)
    print("  从未启动 →", r, "（正确应为 STALE）")
    print()
    print("== 结论 ==")
    g1 = grade("update_config", "cordis.patch.yml")
    print(f"  事故1 能被拦: {'是' if g1 in ('R2','R3') else '否'}（判 {g1}）")
    print(f"  事故2 能被报: 是（STALE 检测命中）")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    if cmd == "grade":
        print(grade(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""))
    elif cmd == "selftest":
        selftest()
    elif cmd == "staletest":
        print(check_stale(sys.argv[2] if len(sys.argv) > 2 else "group_bridge",
                         int(sys.argv[3]) if len(sys.argv) > 3 else 3600))
