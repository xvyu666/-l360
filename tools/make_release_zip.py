# -*- coding: utf-8 -*-
"""把 dist/手机打印助手 打成可直接上传到 GitHub Release 的 zip。

用法：
    python tools/make_release_zip.py            # 默认 v1.0.0
    python tools/make_release_zip.py v1.1.0

输出：
    release/手机打印助手-<版本>-便携版.zip
并打印 SHA256，方便贴到 Release 说明里。
"""
from __future__ import annotations

import hashlib
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "dist", "手机打印助手")
OUT_DIR = os.path.join(HERE, "release")
SKIP_DIR = {"__pycache__", ".git", ".idea", "logs"}


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ver = sys.argv[1] if len(sys.argv) > 1 else "v1.0.0"
    if not os.path.isdir(SRC):
        print("先跑 python build_portable.py 生成 %s" % SRC)
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    dst = os.path.join(OUT_DIR, "手机打印助手-%s-便携版.zip" % ver)

    n = 0
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dp, dn, fn in os.walk(SRC):
            dn[:] = [d for d in dn if d not in SKIP_DIR]
            for f in fn:
                full = os.path.join(dp, f)
                arc = os.path.relpath(full, os.path.dirname(SRC))
                z.write(full, arc)
                n += 1

    size = os.path.getsize(dst)
    print("打包完成 -> %s" % dst)
    print("  文件 %d 个，%.1f MB" % (n, size / 1048576.0))
    print("  SHA256: %s" % sha256(dst))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
