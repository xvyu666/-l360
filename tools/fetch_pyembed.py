# -*- coding: utf-8 -*-
"""下载官方「嵌入式 Python」，供 build_portable.py 打便携版用。

为什么要单独下载：
  runtime/pyembed.zip 有 10MB，不适合进 Git；但它又是打包便携版的必需品。
  所以仓库里只放这个下载脚本，谁要打包谁跑一次。

用法：
    python tools/fetch_pyembed.py            # 默认 3.13.14
    python tools/fetch_pyembed.py 3.12.10    # 指定版本
"""
from __future__ import annotations

import os
import sys
import urllib.request

BASE = "https://www.python.org/ftp/python/{ver}/python-{ver}-embed-amd64.zip"
DEFAULT_VER = "3.13.14"


def main() -> int:
    ver = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VER
    # 打包脚本只认这个文件名
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dst = os.path.join(here, "runtime", "pyembed.zip")
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    url = BASE.format(ver=ver)
    print("下载 %s" % url)
    print("  -> %s" % dst)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = r.read()
    except Exception as e:
        print("下载失败：%s" % e)
        print("可以手动下载后放到上面的路径：%s" % url)
        return 1

    if len(data) < 1024 * 1024:
        print("文件太小（%d 字节），可能下到错误页面了" % len(data))
        return 1

    with open(dst, "wb") as f:
        f.write(data)
    print("完成，%.1f MB" % (len(data) / 1048576.0))
    print("接下来跑：python build_portable.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
