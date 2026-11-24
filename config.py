#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""config.py —— 所有工具共用的一份配置

为什么要有它：
    原来每个脚本都把路径写死在代码里（`C:\\Users\\<你的名字>\\...` 这种），
    别人 `git clone` 下来一个都跑不了，得一个个改。

    现在全部路径从这里读。改一个 `config.json`，十二个脚本一起生效。

用法：
    from config import cfg, expand
    chat_dir = expand(cfg("chat.dir", "~/dsh-group-chat"))
    groups   = cfg("chat.groups", [])

规则：
    1. 值是路径的，一律用 `~` 开头表示用户主目录（跨系统）
    2. 没配就用默认值（不报错，能跑起来）
    3. 组名、端口这些"每个人不一样"的，全在这里配
"""
from __future__ import annotations

import json
import os
import sys

# 配置文件和本模块放一起
_HERE = os.path.dirname(os.path.abspath(__file__))
_CONFIG = os.path.join(_HERE, "config.json")
_EXAMPLE = os.path.join(_HERE, "config.example.json")

_cache = None


def _load():
    global _cache
    if _cache is not None:
        return _cache
    for p in (_CONFIG, _EXAMPLE):
        try:
            with open(p, encoding="utf-8-sig") as f:
                _cache = json.load(f)
                return _cache
        except Exception:
            continue
    _cache = {}
    return _cache


def cfg(key, default=None):
    """读配置，支持点号路径：cfg("chat.port", 8765)"""
    node = _load()
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node if node is not None else default


def expand(p):
    """把路径里的 ~ 换成用户主目录，并统一分隔符"""
    if not p:
        return p
    p = os.path.expanduser(str(p))
    return os.path.abspath(p)


def home():
    """DSH 的 home（默认 ~/.dsh）"""
    return expand(cfg("dsh_home", "~/.dsh"))


def first_existing(*cands):
    """按顺序找第一个存在的路径，都不存在返回最后一个（方便报错时看到期望位置）"""
    for c in cands:
        if c and os.path.exists(expand(c)):
            return expand(c)
    return expand(cands[-1]) if cands else ""


def python_bin():
    """该用哪个 python 跑这些脚本。

    优先级：配置里写的 > 当前解释器（如果它 import 得动）> 报错
    这个函数是为了解决一个真实事故：脚本被 PATH 上的 3.10 跑，
    但依赖是 cp312 编的，报 `No module named numpy._core`。
    """
    p = cfg("python.runtime", "")
    if p:
        return expand(p)
    return sys.executable


def node_bin():
    """该用哪个 node"""
    return expand(cfg("node", "") or "node")


def check():
    """自检：打印读到的配置和几个关键路径存不存在"""
    print("配置文件:", _CONFIG if os.path.exists(_CONFIG) else f"{_EXAMPLE}（示例，建议复制成 config.json）")
    print("DSH home:", home(), "✅" if os.path.exists(home()) else "❌ 不存在")
    for key, dflt in (
        ("chat.dir", "~/dsh-group-chat"),
        ("log_dir", "~/.dsh/logs"),
        ("soul.home", "~/.dsh/soul"),
    ):
        v = expand(cfg(key, dflt))
        print(f"{key:<12}", v, "✅" if os.path.exists(v) else "⚠️ 还没建")
    g = cfg("chat.groups", [])
    print(f"chat.groups  {len(g)} 个: {', '.join(g) if g else '（没配，用示例里的）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(check())
