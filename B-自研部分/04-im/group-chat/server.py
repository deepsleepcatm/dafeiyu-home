#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
群聊 · server.py  —— 多个 AI 会话共用的聊天室

为什么不是"信箱"：信箱是待办清单，群聊是房间。
为什么不用服务器：所有部门在同一台机器上，一个本地 HTTP 服务就够了。

跑法：
    python 群聊\server.py              # 默认 127.0.0.1:8765
    python 群聊\server.py --port 8765 --host 0.0.0.0   # 想用手机看，开 0.0.0.0

打开：  http://127.0.0.1:8765

接口（各组用 chat.py，不用记）：
    GET  /api/messages?after=<id>&limit=200
    POST /api/send        {"sender":"组A","text":"...","at":"组J"}
    POST /api/heartbeat   {"sender":"组A"}
    GET  /api/members

存储：同目录 chat.db（sqlite3，纯标准库，不装任何东西）
"""

import sys

try:                       # 控制台可能是 GBK；别让打印把业务搞挂
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
import json
import os
import sqlite3
import threading
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

HERE = os.path.dirname(os.path.abspath(__file__))
MUTE_FLAG = os.path.join(HERE, "静音.flag")
DB = os.path.join(HERE, "chat.db")
INDEX = os.path.join(HERE, "index.html")
LOCK = threading.Lock()

# 跟桥里的 17 个组对不上，界面就会"少人"。读不到才退回下面这份。
FALLBACK_GROUPS = [
    "主人", "组长",

    "组A", "组B", "组C", "组D",
]


def load_groups():
    try:
        with open(os.path.join(HERE, "bridge-state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        names = list(state.get("sessions", {}).keys())
        if not names:
            return list(FALLBACK_GROUPS)
        # 主人排最前，其余按桥里的顺序
        return ["主人"] + names
    except Exception:
        return list(FALLBACK_GROUPS)


GROUPS = load_groups()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            sender TEXT NOT NULL,
            text TEXT NOT NULL,
            at TEXT DEFAULT '')""")
        conn.execute("""CREATE TABLE IF NOT EXISTS members(
            name TEXT PRIMARY KEY,
            last_seen TEXT NOT NULL)""")


def add_member(name):
    with db() as conn:
        conn.execute("INSERT INTO members(name,last_seen) VALUES(?,?) "
                     "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen",
                     (name, now()))


def fetch_messages(after=0, limit=200, tail=0):
    with db() as conn:
        if tail:
            # 要"最新 N 条"就得倒着取再翻回来，这里单独给一个 tail 参数。
            rows = conn.execute(
                "SELECT id,ts,sender,text,at FROM messages "
                "WHERE COALESCE(scope,'group')='group' ORDER BY id DESC LIMIT ?",
                (tail,)).fetchall()[::-1]
        else:
            rows = conn.execute(
                "SELECT id,ts,sender,text,at FROM messages "
                "WHERE id>? AND COALESCE(scope,'group')='group' ORDER BY id LIMIT ?",
                (after, limit)).fetchall()
    return [{"id": r[0], "ts": r[1], "sender": r[2], "text": r[3], "at": r[4]} for r in rows]


def member_rank(name):
    """按编号排，最后放组长和主人"""
    circles = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭"
    if name and name[0] in circles:
        return (0, circles.index(name[0]), name)
    order = {"组长": (1, 3, "")}
    if name in order:
        return order[name]
    if name in ("主人", "李小娜"):
        return (2, 0 if name == "主人" else 1, "")
    return (3, 0, name)


def fetch_members():
    with db() as conn:
        rows = conn.execute("SELECT name,last_seen FROM members ORDER BY name").fetchall()
    out = []
    for name, last in rows:
        # TG·Lin / QQ·xxx 是"渠道标签"，不是人——不占成员栏
        if name.startswith("TG·") or name.startswith("QQ·"):
            continue
        try:
            t = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
            mins = (datetime.now() - t).total_seconds() / 60
        except Exception:
            mins = 9999
        out.append({"name": name, "last_seen": last, "online": mins < 10})
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "dafeiyu-chat/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache="no-store"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass          # 手机切后台/断线是常态，别让服务端崩

    def do_GET(self):
        from urllib.parse import unquote
        # 中文路径要先解码，否则 /图片/xxx 永远匹配不上（%E5%9B%BE...）
        raw_path = urlparse(self.path).path
        path = unquote(raw_path)
        qs = parse_qs(urlparse(self.path).query)
        if path in ("/", "/index.html"):
            try:
                with open(INDEX, encoding="utf-8") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(500, {"error": "index.html 不在同目录"})
            return
        # 取件箱：电脑上的文件，手机浏览器直接下（2026-10-29 加）
        if path.startswith("/取件"):
            share = os.path.join(HERE, "取件箱")
            os.makedirs(share, exist_ok=True)
            if path in ("/取件", "/取件/"):
                items = []
                for name in sorted(os.listdir(share), reverse=True):
                    p = os.path.join(share, name)
                    if os.path.isfile(p):
                        kb = max(1, round(os.path.getsize(p) / 1024))
                        items.append('<li><a href="/取件/' + quote(name) + '">' + name +
                                     '</a> <span>' + str(kb) + ' KB</span></li>')
                body = ("<!doctype html><meta charset=utf-8>"
                        "<meta name=viewport content='width=device-width,initial-scale=1'>"
                        "<title>取件箱</title>"
                        "<style>body{background:#0d1521;color:#e9eef6;font:16px/1.8 system-ui;padding:20px}"
                        "a{color:#7fd1ff;text-decoration:none}li{margin:14px 0;list-style:none}"
                        "span{color:#8496ad;font-size:13px}h2{font-size:18px}"
                        "em{color:#8496ad;font-size:13px}</style>"
                        "<h2>取件箱</h2><em>把文件丢进 协作\\群聊\\取件箱 就会出现在这里</em><ul>" +
                        ("".join(items) or "<li><em>现在是空的</em></li>") + "</ul>")
                self._send(200, body, "text/html; charset=utf-8")
                return
            name = os.path.basename(unquote(path[len("/取件/"):]))
            p = os.path.join(share, name)
            if os.path.isfile(p):
                with open(p, "rb") as fh:
                    data = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition",
                                 "attachment; filename*=UTF-8''" + quote(name))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._send(404, {"error": "no such file"})
            return
        if path.startswith("/图片/"):
            name = os.path.basename(path[len("/图片/"):])
            p = os.path.join(HERE, "图片", name)
            if os.path.isfile(p):
                ext = os.path.splitext(name)[1].lower()
                mime = {".png": "image/png", ".gif": "image/gif",
                        ".webp": "image/webp"}.get(ext, "image/jpeg")
                with open(p, "rb") as fh:
                    self._send(200, fh.read(), mime, cache="public, max-age=86400")
            else:
                self._send(404, {"error": "no such image"})
            return
        if path == "/api/messages":
            after = int(qs.get("after", ["0"])[0] or 0)
            limit = min(int(qs.get("limit", ["200"])[0] or 200), 500)
            tail = min(int(qs.get("tail", ["0"])[0] or 0), 200)
            self._send(200, {"messages": fetch_messages(after, limit, tail), "members": fetch_members(),
                             "server_time": now()})
            return
        if path == "/api/mute":
            self._send(200, {"muted": os.path.exists(MUTE_FLAG)})
            return

        if path == "/api/members":
            self._send(200, {"members": fetch_members(), "groups": GROUPS})
            return
        self._send(404, {"error": "no such path"})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send(400, {"error": "bad json"})
            return

        if path == "/api/send":
            sender = (payload.get("sender") or "").strip()
            text = (payload.get("text") or "").strip()
            at = (payload.get("at") or "").strip()
            if not sender or not text:
                self._send(400, {"error": "sender 和 text 都要有"})
                return
            with LOCK, db() as conn:
                cur = conn.execute("INSERT INTO messages(ts,sender,text,at) VALUES(?,?,?,?)",
                                   (now(), sender, text, at))
                mid = cur.lastrowid
                conn.execute("INSERT INTO members(name,last_seen) VALUES(?,?) "
                             "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen",
                             (sender, now()))
            self._send(200, {"ok": True, "id": mid})
            return

        if path == "/api/mute":
            want = payload.get("muted")
            if want is None:
                want = not os.path.exists(MUTE_FLAG)
            if want:
                with open(MUTE_FLAG, "w", encoding="utf-8") as fh:
                    fh.write("web 按钮：全员静音 " + now() + "\n")
            else:
                try:
                    os.remove(MUTE_FLAG)
                except FileNotFoundError:
                    pass
                # 顺手清掉没叫完的广播，别解静音后补叫一堆旧的
                try:
                    bq = os.path.join(HERE, "broadcast_queue.json")
                    with open(bq, "w", encoding="utf-8") as fh:
                        fh.write("[]")
                except Exception:
                    pass
            self._send(200, {"ok": True, "muted": os.path.exists(MUTE_FLAG)})
            return

        if path == "/api/heartbeat":
            sender = (payload.get("sender") or "").strip()
            if not sender:
                self._send(400, {"error": "sender 要有"})
                return
            add_member(sender)
            self._send(200, {"ok": True})
            return

        self._send(404, {"error": "no such path"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    init()
    # 服务起来就登记一次"组长/组A"在场，方便界面不是空的
    add_member("组A")

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[{now()}] 群聊已开： http://{args.host}:{args.port}")
    print(f"        数据： {DB}")
    print("        Ctrl+C 停")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停了")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()




