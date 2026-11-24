# -*- coding: utf-8 -*-
"""停摆告警 —— 桥活着但卡住的时候报警并自愈（组A 2026-11-24）

为什么要有它：
    2026-11-24 早上发现群聊桥跑了 23 小时、inFlight 卡死，14 个组从 11-20 起就没被叫醒过。
    看门.py 只管"进程在不在"，进程活着但卡住它判不出来 —— 这就是"沉默不被当成故障"。

判据（不是看进程在不在，是看它有没有在干活）：
    1. 库里存在 id > lastId 的新消息，而 lastId 连续 N 分钟没变  ⇒ 桥卡死
    2. SnowLuma 的 OneBot HTTP(3000) 不通                        ⇒ QQ 上游挂了
    3. 桥进程不存在                                              ⇒ 交给看门.py，这里只记一笔

处置：
    · 桥卡死 → kill 桥进程（看门.py 30 秒内会用同样的命令拉起来，inFlight 随之清空）
    · SnowLuma 不通 → 拉起 D:\\SnowLuma\\launcher.bat（不代它扫码，那步得人做）
    · 每次处置都往群里发一条，说明"发生了什么 + 我做了什么"

用法：
    python 停摆告警.py            # 常驻，每 60 秒一轮
    python 停摆告警.py --once     # 只查一次
"""
from __future__ import annotations



import argparse
import json
import os

# 2026-11-24：群聊端口不写死 —— 环境变量 CHAT_PORT 优先，没设就用 8765。
CHAT_PORT = int(os.environ.get("CHAT_PORT", "8765"))
import socket
import sqlite3
import subprocess
import time
from datetime import datetime

import os, sys
# 往上找到仓库根（有 config.py 那层）
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "config.py")):
    _d = os.path.dirname(_d)
if _d not in sys.path:
    sys.path.insert(0, _d)
from config import cfg, expand

CHAT = expand(cfg("chat.dir", "~/dsh-group-chat"))
DB = os.path.join(CHAT, "chat.db")
STATE = os.path.join(CHAT, "bridge-state.json")
LOG = os.path.join(CHAT, "停摆告警.log")

SNOWLUMA_BAT = r"D:\SnowLuma\launcher.bat"
SNOWLUMA_PORT = 3000

STALL_MINUTES = 5          # lastId 连续这么久没追 ⇒ 判定卡死
CHECK_INTERVAL = 60        # 每 60 秒一轮
CREATE_NO_WINDOW = 0x08000000


def log(msg: str) -> None:
    line = datetime.now().strftime("%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def max_msg_id() -> int:
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT MAX(id) FROM messages").fetchone()
            return int(row[0] or 0)
        finally:
            con.close()
    except Exception as e:
        log(f"读库失败：{e}")
        return -1


def bridge_last_id() -> int:
    try:
        with open(STATE, encoding="utf-8") as f:
            return int(json.load(f).get("lastId") or 0)
    except Exception:
        return -1


def bridge_pids() -> list[int]:
    """找 chat-bridge.mjs 的进程号"""
    try:
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" "
            "| Where-Object { $_.CommandLine -like '*chat-bridge*' } "
            "| Select-Object -ExpandProperty ProcessId"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=25,
            encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW,
        )
        return [int(x) for x in (r.stdout or "").split() if x.strip().isdigit()]
    except Exception:
        return []


def say(text: str) -> None:
    """往群里说一句（组A身份）"""
    try:
        import urllib.request
        payload = json.dumps({"sender": "组A", "text": text, "at": ""}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:%d' % CHAT_PORT/api/send", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=8).read()
    except Exception as e:
        log(f"发群消息失败：{e}")


def restart_bridge(reason: str) -> None:
    pids = bridge_pids()
    if not pids:
        log("桥进程不在 —— 交给看门.py，不动手")
        return
    log(f"处置：重启桥（{reason}），PID {pids}")
    for p in pids:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(p)],
                           capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            log(f"杀 {p} 失败：{e}")
    say(f"【停摆告警】{reason}。已重启群聊桥，看门.py 会在 30 秒内拉起，各组稍后会补收这期间的消息。")


def start_snowluma() -> None:
    if not os.path.exists(SNOWLUMA_BAT):
        log(f"找不到 {SNOWLUMA_BAT}，跳过")
        return
    log("处置：拉起 SnowLuma")
    try:
        # 这俩在 Windows 上是互斥的 —— launcher.bat 最后有 pause，没有控制台就直接退出，
        # 所以告警"检测到了、也尝试拉起了、但就是起不来"（11:49:31 那次就是这么失败的）。
        # 改成跟手动成功的方式一致：新开一个最小化的控制台跑 bat。
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "/min", "", SNOWLUMA_BAT],
            cwd=os.path.dirname(SNOWLUMA_BAT),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        time.sleep(15)
        ok = port_open(SNOWLUMA_PORT)
        if ok:
            say(f"【停摆告警】SnowLuma 掉了（OneBot 端口 {SNOWLUMA_PORT} 不通），已拉起，端口已恢复。")
        else:
            say(f"【停摆告警】SnowLuma 掉过，我拉了但端口 {SNOWLUMA_PORT} 还没起来 —— 可能需要在它的控制台里登录 QQ。")
    except Exception as e:
        log(f"拉起 SnowLuma 失败：{e}")
        say(f"【停摆告警】SnowLuma 挂了，我拉不起来：{e}")


def check_once(stall_since: dict) -> None:
    """返回 None；异常时自己处置"""
    live = max_msg_id()
    last = bridge_last_id()
    pids = bridge_pids()

    # 每小时打一行心跳，方便以后确认"它自己是活的"（不然只有出事才留痕，看不出它有没有在跑）
    hour_key = int(time.time() // 3600)
    if stall_since.get("_beat") != hour_key:
        stall_since["_beat"] = hour_key
        log(f"心跳：库里最新={live} 桥读到={last} 桥进程={len(pids)} SnowLuma3000={port_open(SNOWLUMA_PORT)}")

    # 组XLuma 上游
    if not port_open(SNOWLUMA_PORT):
        stamp = stall_since.get("snowluma", 0)
        if not stamp:
            stall_since["snowluma"] = time.time()
            log(f"SnowLuma {SNOWLUMA_PORT} 不通，开始计时")
        elif time.time() - stamp > STALL_MINUTES * 60:
            start_snowluma()
            stall_since["snowluma"] = time.time()
        return
    else:
        stall_since.pop("snowluma", None)

    # 组X在追消息
    if live < 0 or last < 0:
        return
    if live > last:
        stamp = stall_since.get("bridge", 0)
        if not stamp or stall_since.get("bridge_live") != live:
            stall_since["bridge"] = time.time()
            stall_since["bridge_live"] = live
            log(f"有 {live - last} 条新消息未处理（last={last}），开始计时")
        elif time.time() - stamp > STALL_MINUTES * 60:
            restart_bridge(f"桥 {STALL_MINUTES} 分钟没追新消息（last={last} 落后 {live - last} 条）")
            stall_since["bridge"] = time.time()
    else:
        stall_since.pop("bridge", None)
        stall_since.pop("bridge_live", None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    log(f"=== 停摆告警启动（阈值 {STALL_MINUTES} 分钟，每 {CHECK_INTERVAL} 秒一轮）===")
    stall_since: dict = {}
    while True:
        try:
            check_once(stall_since)
        except Exception as e:
            log(f"本轮出错（不影响下一轮）：{e}")
        if args.once:
            break
        time.sleep(CHECK_INTERVAL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
