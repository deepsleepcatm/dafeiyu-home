#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
看门.py —— 轻量监工：每 30 秒确认关键服务活着，死了就拉（组A 2026-10-31）

为什么要有它：
    计划任务是 5 分钟一次，太慢——群聊断线 5 分钟人就骂了。
    这个每 30 秒看一眼，只做"拉起"，不做别的。

盯：
    8765 网页服务 / 桥 / 简报器 / 心跳 / 守护 / 记账
"""

import os

# 2026-11-24：群聊端口不写死 —— 环境变量 CHAT_PORT 优先，没设就用 8765。
CHAT_PORT = int(os.environ.get("CHAT_PORT", "8765"))

import socket
import subprocess
import sys
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import os, sys
# 往上找到仓库根（有 config.py 那层）
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "config.py")):
    _d = os.path.dirname(_d)
if _d not in sys.path:
    sys.path.insert(0, _d)
from config import cfg, expand

# 全部从 config.json 读（原来写死本机路径，别人 clone 下来一个都跑不了）
PY = expand(cfg("python.runtime", "") or sys.executable)
NODE = expand(cfg("node", "") or "node")
CHAT = expand(cfg("chat.dir", "~/dsh-group-chat"))
TOOLS = _d
BRIDGE_DIR = expand(cfg("chat.bridge_dir", "~/dsh-group-chat"))

LOGDIR = expand(cfg("log_dir", "~/.dsh/logs"))
LOG_HANDLES = []          # 保持文件句柄引用，防止被 GC 关掉（子进程还持有它）


def open_log(name):
    """给服务开日志文件。

    2026-11-24 组A：原来这里是 DEVNULL —— 所有服务的 stdout/stderr 全被丢掉，
    桥的 log()、Python 的 traceback、Node 的异常，一条都没落过盘。
    今天一天的"看不见"（桥卡死不知道卡在哪、#5818 缺 targets 不知道为什么）都是它造成的。

    写不进去就返回 None，调用方退回 DEVNULL —— 保底：宁可没日志，也不能让服务起不来。
    """
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        p = os.path.join(LOGDIR, f"{name}.log")
        # 8 MB 滚动：超了就把旧的改名成 .1（只留一代）
        try:
            if os.path.getsize(p) > 8 * 1024 * 1024:
                bak = p + ".1"
                if os.path.exists(bak):
                    os.remove(bak)
                os.rename(p, bak)
        except OSError:
            pass
        return open(p, "a", encoding="utf-8", errors="replace", buffering=1)
    except Exception:
        return None


# 服务清单从 config.json 的 services.list 读；没配就用下面这份最小示例
_DEFAULT_SERVICES = [
    ("网页", "server.py", [PY, "-u", os.path.join(CHAT, "server.py"), "--host", "0.0.0.0", "--port", str(cfg("chat.port", 8765))], None),
    ("群聊桥", "chat-bridge.mjs", [NODE, os.path.join(BRIDGE_DIR, "src", "chat-bridge.mjs")], BRIDGE_DIR),
]

_RAW_SERVICES = cfg("services.list", None)
if _RAW_SERVICES:
    SERVICES = []
    for s in _RAW_SERVICES:
        cmd = [PY if x == "python" else NODE if x == "node" else x for x in s.get("cmd", [])]
        SERVICES.append((s.get("name", "服务"), s.get("marker", ""), cmd, s.get("cwd")))
else:
    SERVICES = _DEFAULT_SERVICES

_OLD_SERVICES = [
    ("网页", "server.py", [PY, "-u", os.path.join(CHAT, "server.py"), "--host", "0.0.0.0", "--port", "8765"], None),
    ("桥", "chat-bridge.mjs", [NODE, os.path.join(BRIDGE_DIR, "src", "chat-bridge.mjs")], BRIDGE_DIR),
    ("简报", "watch_output.py", [PY, "-u", os.path.join(TOOLS, "watch_output.py")], None),
    ("心跳", "presence_loop.py", [PY, "-u", os.path.join(CHAT, "presence_loop.py")], None),
    ("守护", "守护.py", [PY, "-u", os.path.join(TOOLS, "守护.py")], None),
    ("记账", "记账.py", [PY, "-u", os.path.join(TOOLS, "记账.py"), "--loop"], None),
    ("停摆告警", "停摆告警.py", [PY, "-u", os.path.join(TOOLS, "停摆告警.py")], None),
]


def procs(marker):
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='node.exe'\" "
          "| Where-Object { $_.CommandLine -like '*%s*' } | Measure-Object | Select-Object -ExpandProperty Count" % marker)
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
    try:
        return int((r.stdout or "0").strip() or 0)
    except Exception:
        return -1


def port_ok(p=8765):
    s = socket.socket()
    s.settimeout(1.5)
    try:
        return s.connect_ex(("127.0.0.1", p)) == 0
    finally:
        s.close()


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def main():
    log("看门启动：每 30 秒巡检一次")
    while True:
        for name, marker, cmd, cwd in SERVICES:
            try:
                n = procs(marker)
                # 端口在听就别再起第二个（否则两个副本互相抢）
                if name == "网页" and port_ok(8765):
                    continue
                if n <= 0:
                    log(f"{name} 不在，拉起")
                    lf = open_log(name)
                    kw = {}
                    if lf is not None:
                        kw["stdout"] = lf
                        kw["stderr"] = subprocess.STDOUT
                        LOG_HANDLES.append(lf)     # 防 GC
                        log(f"{name} 日志 → {os.path.join(LOGDIR, name + '.log')}")
                    else:
                        kw["stdout"] = subprocess.DEVNULL
                        kw["stderr"] = subprocess.DEVNULL
                        log(f"{name} 日志目录写不进去，退回 DEVNULL")
                    subprocess.Popen(cmd, cwd=cwd, creationflags=0x08000000, **kw)
                    time.sleep(3)
            except Exception as e:
                log(f"{name} 检查出错：{e}")
        if not port_ok():
            log(f"{CHAT_PORT} 端口不通（可能还在启动）")
        time.sleep(30)


if __name__ == "__main__":
    main()