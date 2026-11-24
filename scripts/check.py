#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check.py —— 提交前的自动检查（CI 也跑这个）

查四样，全过才算干净：

1. **BOM**：任何文本文件开头不能有 \\xef\\xbb\\xbf
   —— BOM 会让 Python/Node 读 JSON 直接失败，也会让 Electron 程序启动即退、连报错都没有
2. **硬路径**：不能出现 `C:\\Users\\<某个具体用户名>` 这类本机路径
   —— 别人 clone 下来一个都跑不了
3. **Python 语法**：每个 .py 都能 compile
4. **JS 语法**：每个 .mjs/.js 都能 `node --check`（没装 node 就跳过）

用法：
    python scripts/check.py          # 全查
    python scripts/check.py --fix    # 顺手去掉 BOM（只做这一项）

退出码：0 全过 / 1 有问题
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEXT_EXT = {".py", ".mjs", ".js", ".json", ".md", ".yml", ".yaml", ".txt", ".sh", ".ps1", ".cmd"}

# 2026-11-24 组A：.ps1 必须带 UTF-8 BOM，否则 Windows PowerShell 5.1 按 GBK 读，
# 中文全变乱码、语法直接报错。这跟「数据文件不能有 BOM」是反的，别一刀切。
PS1_EXEMPT = {".ps1", ".psm1"}
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", "07-memes"}

# 硬路径：C:\Users\<name>、/home/<name>、/Users/<name>
HARD_PATH = re.compile(r"(?:C:\\\\?Users\\\\?|/home/|/Users/)(?!<|你的|\*)[A-Za-z0-9._-]{2,}", re.I)

problems: list[str] = []


def walk():
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for f in fns:
            yield os.path.join(dp, f)


def rel(p):
    return os.path.relpath(p, ROOT).replace("\\", "/")


def check_bom(fix=False):
    n = 0
    for p in walk():
        ext = os.path.splitext(p)[1].lower()
        if ext in PS1_EXEMPT:
            continue          # .ps1 需要 BOM，见文件头说明
        if ext not in TEXT_EXT:
            continue
        with open(p, "rb") as fh:
            head = fh.read(3)
        if head == b"\xef\xbb\xbf":
            if fix:
                raw = open(p, "rb").read()
                with open(p, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(raw.decode("utf-8-sig"))
                print(f"  🔧 去掉了 BOM: {rel(p)}")
            else:
                problems.append(f"BOM: {rel(p)}")
                n += 1
    print(f"  BOM 检查：{'✅ 全部干净' if n == 0 else f'❌ {n} 个文件带 BOM'}")
    return n


def check_hardpaths():
    n = 0
    for p in walk():
        if os.path.splitext(p)[1].lower() not in TEXT_EXT:
            continue
        try:
            text = open(p, encoding="utf-8-sig", errors="ignore").read()
        except OSError:
            continue
        for m in HARD_PATH.finditer(text):
            line = text[: m.start()].count("\n") + 1
            problems.append(f"硬路径: {rel(p)}:{line} → {m.group(0)}")
            n += 1
    print(f"  硬路径检查：{'✅ 零残留' if n == 0 else f'❌ {n} 处'}")
    return n


def check_py():
    n = 0
    for p in walk():
        if not p.endswith(".py"):
            continue
        r = subprocess.run([sys.executable, "-m", "py_compile", p], capture_output=True, text=True)
        if r.returncode != 0:
            problems.append(f"Python 语法: {rel(p)}\n{(r.stderr or r.stdout).strip()[:300]}")
            n += 1
    print(f"  Python 语法：{'✅ 全部通过' if n == 0 else f'❌ {n} 个文件报错'}")
    return n


def check_js():
    node = os.environ.get("NODE") or "node"
    try:
        subprocess.run([node, "--version"], capture_output=True, timeout=10)
    except Exception:
        print("  JS 语法：⚠️ 没装 node，跳过")
        return 0
    n = 0
    for p in walk():
        if not p.endswith((".mjs", ".js")):
            continue
        r = subprocess.run([node, "--check", p], capture_output=True, text=True)
        if r.returncode != 0:
            problems.append(f"JS 语法: {rel(p)}\n{(r.stderr or r.stdout).strip()[:300]}")
            n += 1
    print(f"  JS 语法：{'✅ 全部通过' if n == 0 else f'❌ {n} 个文件报错'}")
    return n


def main():
    fix = "--fix" in sys.argv
    print("=" * 58)
    print("提交前检查")
    print("=" * 58)

    check_bom(fix=fix)
    check_hardpaths()
    check_py()
    check_js()

    print("=" * 58)
    if problems:
        print(f"❌ 有 {len(problems)} 个问题：\n")
        for p in problems[:30]:
            print("  " + p)
        if len(problems) > 30:
            print(f"  ...还有 {len(problems) - 30} 条")
        return 1
    print("✅ 全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
