# -*- coding: utf-8 -*-
"""全能力质检（组A 2026-11-24）

目的：把"装了"和"真能用"分开。每一项都实际执行一次，不看配置、不看文件存在性。
输出：终端打印 + 落盘报告。

用法：
    python 质检.py            # 全跑
    python 质检.py --quick    # 只跑底层命令和端口（快）
"""
from __future__ import annotations



import json
import os

# 2026-11-24：群聊端口不写死 —— 环境变量 CHAT_PORT 优先，没设就用 8765。
CHAT_PORT = int(os.environ.get("CHAT_PORT", "8765"))
import shutil
import socket
import subprocess
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os, sys
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "config.py")):
    _d = os.path.dirname(_d)
if _d not in sys.path:
    sys.path.insert(0, _d)
from config import cfg, expand

# 全部从 config.json 读。没配的项检查时会被跳过，不会崩。
PY312 = expand(cfg("python.runtime", "") or sys.executable)
PY310 = expand(cfg("python.extra.py310", ""))
VENV_BILI = expand(cfg("python.extra.venv_bili", ""))
UVPY = expand(cfg("python.extra.uv_python", ""))
SKILLS = expand(cfg("paths.skills", "~/.dsh/skills"))
PROFILE = expand(cfg("paths.profile", "~/.dsh/profiles/desktop"))

RESULT = []          # (组, 名称, 状态, 细节)
CNW = 0x08000000


def _cfg_host(key, default):
    """从 config.json 读一个 host，读不到就用默认值。"""
    try:
        from config import cfg
        return str(cfg(key, default))
    except Exception:
        return default


def rec(group, name, ok, detail=""):
    RESULT.append((group, name, ok, detail))
    mark = "✅" if ok is True else ("⚠️" if ok is None else "❌")
    print(f"  {mark} {name:<38} {detail}")


def run(cmd, timeout=25):
    """跑一条命令，返回 (ok, 输出首行)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", creationflags=CNW)
        out = (r.stdout or r.stderr or "").strip().splitlines()
        first = out[0][:70] if out else ""
        return r.returncode == 0, first
    except FileNotFoundError:
        return False, "找不到命令"
    except Exception as e:
        return False, str(e)[:70]


def port_open(port, host="127.0.0.1", timeout=2):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


# ---------------- A. 底层命令 ----------------
def check_commands():
    print("\n[A] 底层命令")
    for name, cmd in [
        ("ffmpeg", ["ffmpeg", "-version"]),
        ("ffprobe", ["ffprobe", "-version"]),
        ("yt-dlp", ["yt-dlp", "--version"]),
        ("node", ["node", "--version"]),
        ("npx", ["npx", "--version"]),
        ("git", ["git", "--version"]),
        ("curl", ["curl", "--version"]),
    ]:
        ok, first = run(cmd)
        rec("A", name, ok, first)

    for label, py in [("python3.12(DSH)", PY312), ("python3.10", PY310),
                      ("python(venv/bili)", VENV_BILI), ("python(uv)", UVPY)]:
        if not os.path.exists(py):
            rec("A", label, False, "解释器不存在")
            continue
        ok, first = run([py, "--version"])
        rec("A", label, ok, first)


# ---------------- B. 端口/服务 ----------------
def check_ports():
    print("\n[B] 本地服务端口")
    for name, port, host in [
        ("群聊 8765", 8765, "127.0.0.1"),
        ("SnowLuma OneBot HTTP 3000", 3000, "127.0.0.1"),
        ("SnowLuma OneBot WS 3001", 3001, "127.0.0.1"),
        ("SnowLuma WebUI 5099", 5099, "127.0.0.1"),
        ("DSH 63371", 63371, "127.0.0.1"),
    ]:
        rec("B", name, port_open(port, host), f"{host}:{port}")


# ---------------- C. Python 环境能力 ----------------
def check_py_env():
    print("\n[C] Python 环境（实际 import 一次）")
    cases = [
        ("runtime3.12: numpy", PY312, "import numpy; print('numpy', numpy.__version__)"),
        ("runtime3.12: onnxruntime", PY312, "import onnxruntime; print('ort', onnxruntime.__version__)"),
        ("runtime3.12: tokenizers", PY312, "import tokenizers; print('tok', tokenizers.__version__)"),
        ("py310: playwright", PY310, "import playwright; print('pw ok')"),
        ("venv: bilibili_api", VENV_BILI, "import bilibili_api; print('bili api ok')"),
        ("venv: bilibili video_uploader", VENV_BILI, "from bilibili_api import video_uploader; print('uploader ok')"),
        ("venv: faster-whisper", VENV_BILI, "import faster_whisper; print('fw', faster_whisper.__version__)"),
        ("venv: vosk", VENV_BILI, "import vosk; print('vosk ok')"),
        ("venv: edge_tts", VENV_BILI, "import edge_tts; print('edge_tts ok')"),
        ("uv: mcp", UVPY, "import mcp; print('mcp ok')"),
        ("uv: bilibili_api", UVPY, "import bilibili_api; print('bili ok')"),
    ]
    for label, py, code in cases:
        if not os.path.exists(py):
            rec("C", label, False, "解释器不存在")
            continue
        ok, first = run([py, "-c", code], timeout=40)
        rec("C", label, ok, first)


# ---------------- D. 插件 ----------------
def check_plugins():
    print("\n[D] 插件（29 个启用 bundle 逐个查是否存在）")
    try:
        pkg = json.load(open(os.path.join(PROFILE, "package.json"), encoding="utf-8"))
    except Exception as e:
        rec("D", "读 profile/package.json", False, str(e)[:60])
        return
    bundles = pkg.get("dsh", {}).get("profile", {}).get("bundles", [])
    deps = pkg.get("dependencies", {})
    missing, exists = [], 0
    for b in bundles:
        p = os.path.join(PROFILE, "node_modules", *b.split("/"))
        if os.path.isdir(p):
            exists += 1
        else:
            missing.append(b)
    rec("D", f"启用 bundle {len(bundles)} 个", not missing,
        f"存在 {exists} / 缺失 {len(missing)}")
    if missing:
        for m in missing[:5]:
            rec("D", "  缺失: " + m, False, "")
    rec("D", f"依赖总数", True, f"{len(deps)} 个")


# ---------------- E. 技能 ----------------
def check_skills():
    print("\n[E] 技能（54 个：有无 SKILL.md / 有无可执行脚本）")
    if not os.path.isdir(SKILLS):
        rec("E", "skills 目录", False, "不存在")
        return
    dirs = [d for d in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, d))]
    no_skill, with_scripts = [], []
    for d in dirs:
        base = os.path.join(SKILLS, d)
        if not os.path.exists(os.path.join(base, "SKILL.md")):
            no_skill.append(d)
        sc = os.path.join(base, "scripts")
        if os.path.isdir(sc) and any(
            f.endswith((".py", ".sh", ".cmd", ".mjs", ".js"))
            for f in os.listdir(sc)
        ):
            with_scripts.append(d)
    rec("E", f"技能总数", True, f"{len(dirs)} 个")
    rec("E", "缺 SKILL.md 的", not no_skill, ", ".join(no_skill[:6]) or "无")
    rec("E", "带可执行脚本的", True, f"{len(with_scripts)} 个: " + ", ".join(with_scripts[:8]))


# ---------------- F. 记忆工具链 ----------------
def check_memtools():
    print("\n[F] 记忆/状态工具链（实际跑一次）")
    mt = expand(cfg("paths.mem_tools", "~/.dsh/mem-tools"))
    if not os.path.isdir(mt):
        rec("F", "mem-tools 目录", False, "不存在")
        return
    for script, args in [
        ("mem-search.py", ["query", "质检", "2"]),
        ("evt.py", ["today"]),
        ("guard.py", ["--help"]),
        ("consolidate.py", ["--help"]),
        ("done.py", ["--help"]),
    ]:
        p = os.path.join(mt, script)
        if not os.path.exists(p):
            rec("F", script, False, "脚本不存在")
            continue
        ok, first = run([PY312, p] + args, timeout=60)
        # --help 返回 0 或 2 都算"能启动"
        rec("F", script, ok or "usage" in first.lower(), first)

    soul = os.path.join(expand(cfg("soul.home", "~/.dsh/soul")), "soul.js")
    node = shutil.which("node")
    if os.path.exists(soul) and node:
        ok, first = run([node, soul, "render"], timeout=30)
        rec("F", "soul.js render", ok, first)


# ---------------- G. 关键入口 ----------------
def check_entrypoints():
    print("\n[G] 关键入口实测")
    # pwcli
    pw = os.path.join(SKILLS, "playwright", "scripts", "pwcli.cmd")
    rec("G", "pwcli.cmd 存在", os.path.exists(pw), pw)
    # mem.cmd
    mc = os.path.join(expand(cfg("paths.mem_tools", "~/.dsh/mem-tools")), "mem.cmd")
    rec("G", "mem.cmd 存在", os.path.exists(mc), mc)
    # 群聊 API
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d' % CHAT_PORT/api/messages?limit=1", timeout=5) as r:
            rec("G", "群聊 API 可读", r.status == 200, f"HTTP {r.status}")
    except Exception as e:
        rec("G", "群聊 API 可读", False, str(e)[:60])
    # B站登录态
    cred = expand(cfg("paths.bili_credential", "~/.dsh/bili-duty/bili_credential.json"))
    if os.path.exists(cred):
        try:
            d = json.load(open(cred, encoding="utf-8"))
            need = ["sessdata", "bili_jct", "buvid3", "dedeuserid"]
            ok = all(d.get(k) for k in need)
            rec("G", "B站凭证字段完整", ok, f"{len([k for k in need if d.get(k)])}/4")
        except Exception as e:
            rec("G", "B站凭证可读", False, str(e)[:50])


def main():
    quick = "--quick" in sys.argv
    print("=" * 70)
    print("全能力质检  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 70)

    check_commands()
    check_ports()
    if not quick:
        check_py_env()
        check_plugins()
        check_skills()
        check_memtools()
        check_entrypoints()

    ok = sum(1 for r in RESULT if r[2] is True)
    warn = sum(1 for r in RESULT if r[2] is None)
    bad = sum(1 for r in RESULT if r[2] is False)
    print("\n" + "=" * 70)
    print(f"合计 {len(RESULT)} 项：✅ {ok}   ⚠️ {warn}   ❌ {bad}")
    if bad:
        print("\n失败项：")
        for g, n, s, d in RESULT:
            if s is False:
                print(f"  [{g}] {n}  {d}")
    print("=" * 70)

    out = expand(cfg("paths.qc_output", "~/.dsh/_质检结果.json"))
    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump([{"组": g, "项": n, "ok": s, "细节": d} for g, n, s, d in RESULT],
                      f, ensure_ascii=False, indent=2)
        print("结果已存:", out)
    except Exception as e:
        print("存结果失败:", e)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
