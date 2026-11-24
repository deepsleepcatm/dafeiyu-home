"""done.py —— 「做完了没 + 把握几分」的机器判定（Jev 的 Noul 思路，本地实现）

为什么要它：主模型读一堆输出自己下结论，会看错（今天就发生过：管道 exit 1 被我当成失败）。
这个脚本只回三样：成没成、把握几分、证据是什么。判断靠证据，不靠自述。

用法：
  done.py file  <路径>                     文件/目录在不在、多大、什么时候改的
  done.py cmd   <命令> [参数...]            跑命令看退出码（自动提示管道截断假信号）
  done.py msg   <关键词> [--n 20]          群里最近 N 条有没有这条（判断"发出去了没"）
  done.py evt   <kind|any>                 今天的事件流里有没有这类
  done.py all   <spec.json>                多项一起判，取最弱的那个当总把握

输出（默认人类可读，加 --json 出机器格式）：
  成/不成 · 把握 0.00-1.00 · 证据一行

exit code: 0 = 成（把握 >= 0.5）；1 = 不成；2 = 参数错
"""
import sys, os, json, time, sqlite3, subprocess
from pathlib import Path
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from config import cfg, expand   # noqa: E402


DB = expand(cfg("chat.db", "~/dafeiyu/协作/群聊/chat.db"))
EVT = Path(expand(cfg("runtime.events", "~/.dsh/runtime/events.jsonl")))
if not EVT.exists():
    # 事件流也可能落在 workspace，退一步找
    for c in [Path(expand(cfg("intent.events", "~/dafeiyu/意图/events.jsonl")))]:
        if c.exists():
            EVT = c
            break

RESULT = {"ok": False, "confidence": 0.0, "evidence": "", "why": ""}


def _pack(ok, conf, evidence, why):
    RESULT.update(ok=bool(ok), confidence=round(float(conf), 2), evidence=evidence, why=why)
    return RESULT


def check_file(path):
    p = Path(path)
    if not p.exists():
        return _pack(False, 0.95, f"不存在: {path}", "路径不存在就是没做成，这是最硬的判据")
    if p.is_dir():
        n = len(list(p.iterdir()))
        return _pack(n > 0, 0.95 if n else 0.7, f"目录存在，{n} 个条目", "空目录按未完成算")
    st = p.stat()
    kb = st.st_size / 1024
    mt = time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime))
    if st.st_size == 0:
        return _pack(False, 0.75, f"文件存在但 0 字节，改于 {mt}", "0 字节多半是写失败")
    return _pack(True, 0.99, f"{kb:.1f} KB，改于 {mt}", "文件在且有内容")


def check_cmd(cmd_argv):
    t0 = time.time()
    try:
        r = subprocess.run(cmd_argv, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
    except Exception as e:
        return _pack(False, 0.9, f"调用失败: {e}", "命令根本没跑起来")
    out = (r.stdout or "") + (r.stderr or "")
    tail = " ".join(out.strip().splitlines()[-1:])[:160]
    dur = time.time() - t0
    if r.returncode == 0:
        return _pack(True, 0.95, f"exit=0，{dur:.1f}s｜{tail}", "退出码 0")
    # 非零要区分：是管道截断还是真失败
    hint = ""
    if not sys.stdout.isatty() and "Select-Object" in " ".join(cmd_argv):
        hint = "（疑似管道截断：N ≤ 行数时 exit 会变 -1，别当失败）"
    return _pack(False, 0.85, f"exit={r.returncode}，{dur:.1f}s｜{tail}{hint}",
                 "退出码非零；但要先排除管道截断")


def check_msg(keyword, n=20):
    if not Path(DB).exists():
        return _pack(False, 0.6, f"找不到群库 {DB}", "没法判断，按未完成算")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = list(con.execute(
            "select id, ts, sender, text from messages order by id desc limit ?", (n,)))
    finally:
        con.close()
    for mid, ts, sender, text in rows:
        if keyword in (text or ""):
            return _pack(True, 0.99, f"#{mid} {ts[11:16]} {sender} 含「{keyword}」", "在库里查到了")
    latest = rows[0] if rows else None
    if latest and keyword in (latest[3] or ""):
        return _pack(True, 0.99, f"#{latest[0]}", "最新一条就命中")
    return _pack(False, 0.8, f"最近 {len(rows)} 条里没有「{keyword}」", "库里没查到，多半没发出去")


def check_evt(kind="any"):
    if not EVT.exists():
        return _pack(False, 0.6, f"找不到事件流 {EVT}", "没文件就判不了")
    today = time.strftime("%Y-%m-%d")
    hit = 0
    for line in EVT.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if str(e.get("ts", "")).startswith(today) and (kind == "any" or e.get("kind") == kind):
            hit += 1
    if hit:
        return _pack(True, 0.9, f"今天 {kind} 有 {hit} 条", "事件流里有记录")
    return _pack(False, 0.7, f"今天 {kind} 0 条", "0 条 = 要么没干，要么干了没记")


def check_all(spec_path):
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    items = spec if isinstance(spec, list) else spec.get("items", [])
    outs = []
    for it in items:
        chk = it.get("check")
        if chk == "file":
            r = check_file(it["path"])
        elif chk == "cmd":
            r = check_cmd(it["argv"])
        elif chk == "msg":
            r = check_msg(it["keyword"], int(it.get("n", 20)))
        elif chk == "evt":
            r = check_evt(it.get("kind", "any"))
        else:
            r = _pack(False, 0.1, f"未知检查类型 {chk}", "配置写错")
        outs.append({"name": it.get("name", chk), **r})
    if not outs:
        return _pack(False, 0.1, "空 spec", "没东西可判")
    worst = min(outs, key=lambda x: x["confidence"] if x["ok"] else -1)
    ok = all(o["ok"] for o in outs)
    conf = min(o["confidence"] for o in outs)
    return _pack(ok, conf, f"{len(outs)} 项，最弱的是「{worst['name']}」", json.dumps(outs, ensure_ascii=False))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    chk = argv[1]
    if chk == "file":
        r = check_file(argv[2])
    elif chk == "cmd":
        r = check_cmd(argv[2:])
    elif chk == "msg":
        n = 20
        if "--n" in argv:
            i = argv.index("--n")
            n = int(argv[i + 1])
            argv = argv[:i] + argv[i + 2:]
        r = check_msg(argv[2], n)
    elif chk == "evt":
        r = check_evt(argv[2] if len(argv) > 2 else "any")
    elif chk == "all":
        r = check_all(argv[2])
    else:
        print(f"未知检查: {chk}")
        return 2
    if as_json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(f"{'成' if r['ok'] else '不成'}  把握 {r['confidence']:.2f}")
        print(f"  证据：{r['evidence']}")
        print(f"  依据：{r['why']}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
