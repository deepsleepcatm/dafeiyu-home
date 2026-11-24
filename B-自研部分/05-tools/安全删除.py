#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安全删除.py —— 批量删除的闸门（组A 2026-11-24）

为什么要有它：
    《文件操作约束方案_防误删》写了三道闸，但**只有文档、没有代码**。
    也就是说：任何人想批量删东西，没有任何东西会拦住他。
    2026-11-24 11:20 组C按「目录时间戳」删了 86 个会话，
    把 5 个组在用的工作会话一起删了 —— 全是靠 E 盘备份救回来的。
    **规矩写在文档里靠记性，记性靠不住。这个脚本就是那道闸。**

三道闸（对应方案里的三条）：
    闸 1　默认 dry-run，只列清单不删
    闸 2　按体积分层确认：<10MB 放行 / ≥10MB 要 --confirm / ≥100MB 要 --owner-ok
    闸 3　删之前先打包到回收站，打包校验通过才删

用法：
    # 只看会删什么（默认就是这个，什么都不改）
    python 安全删除.py <路径...>

    # 小于 10 MB，直接删（但仍然先打包）
    python 安全删除.py <路径...> --yes --reason "清临时文件"

    # 10 MB 以上，要双确认
    python 安全删除.py <路径...> --yes --confirm --reason "..."

    # 100 MB 以上，要主人点头
    python 安全删除.py <路径...> --yes --confirm --owner-ok --reason "..."

    # 只打包不删（留个后手）
    python 安全删除.py <路径...> --pack-only

    # 从回收站恢复
    python 安全删除.py --restore <批次id>
    python 安全删除.py --list-batches
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 放哪儿：跟脚本同级的 `_回收站`
HERE = os.path.dirname(os.path.abspath(__file__))
TRASH = os.path.join(HERE, "_回收站")
LEDGER = os.path.join(TRASH, "删除账本.jsonl")

# 阈值（对应方案里的三条线）
T_OK = 10 * 1024 * 1024         # <10 MB 放行
T_CONFIRM = 10 * 1024 * 1024    # ≥10 MB 要 --confirm
T_OWNER = 100 * 1024 * 1024     # ≥100 MB 要 --owner-ok


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def collect(paths):
    """把路径展开成文件清单。返回 [(路径, 大小, 最后写入)]"""
    out = []
    for p in paths:
        p = os.path.abspath(p)
        if not os.path.exists(p):
            print(f"  ⚠️ 不存在，跳过：{p}")
            continue
        if os.path.isfile(p):
            st = os.stat(p)
            out.append((p, st.st_size, st.st_mtime))
        else:
            for dp, _, fns in os.walk(p):
                for fn in fns:
                    fp = os.path.join(dp, fn)
                    try:
                        st = os.stat(fp)
                        out.append((fp, st.st_size, st.st_mtime))
                    except OSError:
                        pass
    return out


def show(files, verbose=True):
    total = sum(s for _, s, _ in files)
    print(f"\n将删除 {len(files)} 个文件，共 {human(total)}")
    if verbose and files:
        # 按顶层目录归组，看归属
        groups = {}
        for p, s, m in files:
            # 找第一个像"包名/项目名"的层级
            parts = p.split(os.sep)
            key = os.sep.join(parts[:4]) if len(parts) > 4 else os.sep.join(parts[:-1])
            groups.setdefault(key, [0, 0])
            groups[key][0] += 1
            groups[key][1] += s
        print("\n按归属分组：")
        for k, (n, s) in sorted(groups.items(), key=lambda x: -x[1][1])[:10]:
            print(f"  {human(s):>10}  {n:>5} 个   {k}")
        # 最近改过的（提醒：可能还是活的）
        newest = sorted(files, key=lambda x: -x[2])[:5]
        if newest:
            print("\n最近修改的 5 个（这些可能还是活的，别瞎删）：")
            for p, s, m in newest:
                t = datetime.fromtimestamp(m).strftime("%m-%d %H:%M")
                print(f"  {t}  {human(s):>9}  {p}")
    return total


def pack(files, reason):
    """打包到回收站，返回批次 id 和压缩包路径"""
    os.makedirs(TRASH, exist_ok=True)
    bid = datetime.now().strftime("%Y%m%d-%H%M%S")
    tar_path = os.path.join(TRASH, f"批次-{bid}.tar.gz")

    print(f"\n打包到回收站：{tar_path}")
    # 用公共前缀做相对路径，避免把 C:\ 整棵树塞进包
    common = os.path.commonpath([p for p, _, _ in files]) if len(files) > 1 else os.path.dirname(files[0][0])
    n = 0
    with tarfile.open(tar_path, "w:gz") as tf:
        for p, _, _ in files:
            try:
                arc = os.path.relpath(p, common)
                tf.add(p, arcname=arc)
                n += 1
            except Exception as e:
                print(f"  ⚠️ 打包失败：{p}：{e}")
    size = os.path.getsize(tar_path)
    print(f"  打包完成：{n} 个文件 → {human(size)}")

    # 校验：包里的成员数要对得上
    with tarfile.open(tar_path, "r:gz") as tf:
        members = len(tf.getmembers())
    if members != n:
        raise RuntimeError(f"打包校验失败：期望 {n} 个成员，实际 {members} 个")

    meta = {
        "batch": bid,
        "at": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
        "files": len(files),
        "bytes": sum(s for _, s, _ in files),
        "tarball": tar_path,
        "common_root": common,
        "paths": [p for p, _, _ in files][:200],   # 账本里最多存 200 条路径
        "paths_truncated": len(files) > 200,
    }
    os.makedirs(TRASH, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
    return bid, tar_path, common


def really_delete(files):
    n = 0
    errs = 0
    for p, _, _ in files:
        try:
            os.remove(p)
            n += 1
        except OSError as e:
            errs += 1
            print(f"  ⚠️ 删不掉：{p}：{e}")
    print(f"\n删除完成：{n} 个成功，{errs} 个失败")
    # 把变成空目录的父目录也清掉（只往上 3 层，别乱删）
    for p, _, _ in files:
        d = os.path.dirname(p)
        for _ in range(3):
            try:
                if os.path.isdir(d) and not os.listdir(d):
                    os.rmdir(d)
                    d = os.path.dirname(d)
                else:
                    break
            except OSError:
                break


def restore(bid):
    """从回收站恢复一个批次"""
    if not os.path.exists(LEDGER):
        print("没有删除账本")
        return 1
    rec = None
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            try:
                j = json.loads(line)
                if j.get("batch") == bid:
                    rec = j
            except Exception:
                pass
    if not rec:
        print(f"账本里没有批次 {bid}")
        return 1
    tar_path = rec["tarball"]
    root = rec["common_root"]
    if not os.path.exists(tar_path):
        print(f"压缩包不见了：{tar_path}")
        return 1
    print(f"恢复到：{root}")
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(root)
    print(f"  ✅ 恢复完成（{rec['files']} 个文件）")
    return 0


def list_batches():
    if not os.path.exists(LEDGER):
        print("还没有删除记录")
        return 0
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            try:
                j = json.loads(line)
                print(f"  {j['batch']}  {j['at']}  {j['files']} 个  {human(j['bytes']):>9}  {j.get('reason','')}")
            except Exception:
                pass
    return 0


def main():
    ap = argparse.ArgumentParser(description="批量删除闸门：默认只列清单，不删")
    ap.add_argument("paths", nargs="*", help="要删的路径")
    ap.add_argument("--yes", action="store_true", help="真删（不加就是 dry-run）")
    ap.add_argument("--confirm", action="store_true", help="≥10MB 的中等风险确认")
    ap.add_argument("--owner-ok", action="store_true", help="≥100MB 的高风险确认（主人点头）")
    ap.add_argument("--reason", default="", help="为什么删（会记账）")
    ap.add_argument("--pack-only", action="store_true", help="只打包不删")
    ap.add_argument("--restore", metavar="BATCH", help="从回收站恢复一个批次")
    ap.add_argument("--list-batches", action="store_true", help="列出回收站里的批次")
    a = ap.parse_args()

    if a.restore:
        return restore(a.restore)
    if a.list_batches:
        return list_batches()
    if not a.paths:
        ap.print_help()
        return 2

    print("=" * 62)
    print("安全删除 · 三道闸")
    print("=" * 62)

    files = collect(a.paths)
    if not files:
        print("\n没有可删的文件")
        return 0

    total = show(files)

    # ── 闸 2：按体积分层确认 ──
    if a.pack_only:
        print("\n--pack-only：只打包，不删")
        bid, tar, root = pack(files, a.reason or "pack-only")
        print(f"\n批次号：{bid}")
        print(f"想恢复：python 安全删除.py --restore {bid}")
        return 0

    if not a.yes:
        print("\n" + "=" * 62)
        print("⚠️ 这是 dry-run，什么都没删。")
        print("   确认要删，加 --yes（≥10MB 还要 --confirm，≥100MB 还要 --owner-ok）")
        print("=" * 62)
        return 0

    if total >= T_OWNER and not a.owner_ok:
        print(f"\n❌ 拒绝执行：{human(total)} ≥ 100 MB，属高风险。")
        print("   要删请加 --owner-ok（表示主人已经点头）")
        return 3
    if total >= T_CONFIRM and not a.confirm:
        print(f"\n❌ 拒绝执行：{human(total)} ≥ 10 MB，属中等风险。")
        print("   要删请加 --confirm")
        return 3

    if not a.reason.strip():
        print("\n❌ 拒绝执行：必须给 --reason（写清为什么删）。")
        print("   这条不是为了形式 —— 出事后账本里第一眼看的就是它。")
        return 3

    # ── 闸 3：先打包，校验通过才删 ──
    bid, tar, root = pack(files, a.reason)
    print(f"\n批次号：{bid}")
    print(f"想恢复：python 安全删除.py --restore {bid}")
    really_delete(files)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
