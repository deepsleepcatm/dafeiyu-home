# -*- coding: utf-8 -*-
"""表情包图库：去重 + 合并成一个包（组A 2026-11-24）

规矩（按《文件操作约束方案》）：
  1. 默认 dry-run，只列清单不删
  2. 真删要 --yes
  3. 旧包不直接删，移到备份目录
  4. 不压格式（主人明确说了）

用法：
    python 图库整理.py            # 只看会做什么
    python 图库整理.py --yes      # 真做
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
from collections import defaultdict

import os, sys
# 往上找到仓库根（有 config.py 那层）
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "config.py")):
    _d = os.path.dirname(_d)
if _d not in sys.path:
    sys.path.insert(0, _d)
from config import cfg, expand
SRC = expand(cfg("meme.packs_dir", "~/.dsh/meme-packs"))
DST_PACK = os.path.join(SRC, "dafeiyu")
BACKUP = os.path.join(SRC, "_旧包备份_20261124")
EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

DRY = "--yes" not in sys.argv


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if not os.path.isdir(SRC):
        print("图库目录不存在:", SRC)
        return 1

    # 收集所有图
    # 结果 `dafeiyu-002/003/004` 这些路径里都含 "dafeiyu"，被一并跳过了 ——
    # 扫到 334 张（只剩 merged），实际是 662 张。字符串包含不能当路径判断。
    skip_norm = [os.path.normcase(os.path.abspath(p)) for p in (BACKUP, DST_PACK)]

    def is_skipped(d):
        dn = os.path.normcase(os.path.abspath(d))
        return any(dn == s or dn.startswith(s + os.sep) for s in skip_norm)

    files = []
    for dp, _, fns in os.walk(SRC):
        if is_skipped(dp):
            continue
        for fn in fns:
            if fn.lower().endswith(EXTS):
                p = os.path.join(dp, fn)
                files.append((p, os.path.getsize(p)))

    print(f"扫描到 {len(files)} 张图，{sum(s for _, s in files) / 1024 / 1024:.1f} MB")

    # 按内容 hash 去重（同内容只留一份）
    seen = {}
    keep, dups = [], []
    for p, s in sorted(files, key=lambda x: x[0]):
        h = md5(p)
        if h in seen:
            dups.append((p, seen[h], s))
        else:
            seen[h] = p
            keep.append((p, s))

    dup_bytes = sum(s for _, _, s in dups)
    print(f"\n去重结果：")
    print(f"  保留 {len(keep)} 张  {sum(s for _, s in keep) / 1024 / 1024:.1f} MB")
    print(f"  重复 {len(dups)} 张  {dup_bytes / 1024 / 1024:.1f} MB（这些不进新包）")

    if dups:
        print(f"\n前 10 个重复（保留 → 跳过）：")
        for p, keep_p, s in dups[:10]:
            print(f"  {os.path.relpath(keep_p, SRC)}")
            print(f"    跳过 {os.path.relpath(p, SRC)}  ({s / 1024:.0f} KB)")

    # 同名不同内容的情况要提示（避免覆盖）
    names = defaultdict(list)
    for p, _ in keep:
        names[os.path.basename(p)].append(p)
    clash = {n: ps for n, ps in names.items() if len(ps) > 1}
    if clash:
        print(f"\n⚠️ 有 {len(clash)} 个同名但内容不同的文件，放进同一个包会重名：")
        for n, ps in list(clash.items())[:5]:
            print(f"  {n}  x{len(ps)}")

    if DRY:
        print("\n=== 这是 dry-run，什么都没改 ===")
        print("要真做，加 --yes")
        return 0

    # 组X
    os.makedirs(DST_PACK, exist_ok=True)
    print(f"\n建新包: {DST_PACK}")

    # 组X*保留 memes/<情绪>/ 这一层结构**，平铺会让按情绪搜图失效）
    def tag_of(p):
        """从路径里提取情绪 tag：.../memes/<tag>/xxx.jpg → tag"""
        parts = os.path.normpath(p).split(os.sep)
        if "memes" in parts:
            i = parts.index("memes")
            if i + 1 < len(parts) - 1:
                return parts[i + 1]
        return ""   # 没有 tag 的（比如 collected 根目录散图）放根

    used = set()
    copied = 0
    for p, s in keep:
        tag = tag_of(p)
        outdir = os.path.join(DST_PACK, "memes", tag) if tag else DST_PACK
        os.makedirs(outdir, exist_ok=True)
        base = os.path.basename(p)
        name, ext = os.path.splitext(base)
        final = base
        i = 1
        while os.path.join(outdir, final) in used:
            final = f"{name}_{i}{ext}"
            i += 1
        used.add(os.path.join(outdir, final))
        shutil.copy2(p, os.path.join(outdir, final))
        copied += 1
    print(f"  复制进新包: {copied} 张（保留 memes/<情绪>/ 结构）")

    # 组X备份（不删）
    os.makedirs(BACKUP, exist_ok=True)
    moved = []
    for d in os.listdir(SRC):
        full = os.path.join(SRC, d)
        if not os.path.isdir(full):
            continue
        if full in (DST_PACK, BACKUP):
            continue
        shutil.move(full, os.path.join(BACKUP, d))
        moved.append(d)
    print(f"  旧包移到备份: {moved}")

    # 组X
    new_files = [f for f in os.listdir(DST_PACK) if f.lower().endswith(EXTS)]
    new_size = sum(os.path.getsize(os.path.join(DST_PACK, f)) for f in new_files)
    print(f"\n=== 完成 ===")
    print(f"  新包 dafeiyu: {len(new_files)} 张  {new_size / 1024 / 1024:.1f} MB")
    print(f"  旧包在: {BACKUP}")
    print(f"  想回滚：把旧包从备份目录移回来就行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
