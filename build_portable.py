# -*- coding: utf-8 -*-
"""把打印服务打包成一个免装 Python 的绿色文件夹。

目标机器上不需要装 Python、不需要联网、不写注册表，双击 bat 就能跑。
做法：官方嵌入式 Python + 只搬这次真正用到的几个依赖（不是整个 site-packages）。
"""
import os
import shutil
import sys
import zipfile
import fnmatch

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist", "手机打印助手")
RUNTIME = os.path.join(DIST, "runtime")
APP = os.path.join(DIST, "app")
PKGS = os.path.join(RUNTIME, "Lib", "site-packages")

SP = os.path.join(os.path.dirname(sys.prefix), "Lib", "site-packages")
if not os.path.isdir(SP):
    SP = os.path.join(sys.prefix, "Lib", "site-packages")

# 真正需要的包（pythonwin 是 pywin32 的 GUI 部分，用不到，省 4MB）
NEED_PKG = [
    "PIL", "pypdfium2", "pypdfium2_raw", "pypdfium2_cfg",
    "win32", "win32com", "win32comext", "pillow_heif",
    "pywin32_system32",
]
# pywin32.pth 负责把 win32 / win32\lib / pythonwin 挂进 sys.path，
# 缺了它就到处是 No module named 'win32print'。
# （它最后那行 import pywin32_bootstrap 在 win32\lib\ 下，已随 win32 目录一起过去）
NEED_FILE = ["pywin32.pth"]
NEED_PY = [
    "server.py", "printer_core.py", "renderer.py", "layout.py",
    "native.py", "wechat.py", "office.py", "selftest.py", "inbox.py",
    "notes.py", "htmlnote.py",
]
SKIP_EXT = {".pyc", ".pyo"}
SKIP_DIR = {"__pycache__", ".git", ".idea", "logs"}


def rmtree(p):
    # 本机 rm 会走 safe-bin shim 报错，删目录一律用 shutil
    if os.path.isdir(p):
        shutil.rmtree(p, ignore_errors=True)


def copy_tree(src, dst):
    n = 0
    for dp, dn, fn in os.walk(src):
        dn[:] = [d for d in dn if d not in SKIP_DIR]
        for f in fn:
            if os.path.splitext(f)[1] in SKIP_EXT:
                continue
            s = os.path.join(dp, f)
            rel = os.path.relpath(s, src)
            dpath = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(dpath), exist_ok=True)
            shutil.copy2(s, dpath)
            n += 1
    return n


def main():
    print("=" * 70)
    print("打包 手机打印助手 便携版")
    print("=" * 70)
    print("源 site-packages:", SP)

    # 这里不再整目录删 dist 了。原因很实际：
    #   runtime/ 里是嵌入式 Python 加 site-packages，好几百 MB、上千个文件，
    #   本机 safe-delete 见到 50 个以上的批量删除就要人工确认，整条打包流程
    #   会当场卡死（实测就是这个现象）。
    #   而且 runtime 几乎不变 —— 每次真正变的就是 app/ 下那几个自己写的 .py。
    #   copy_tree 本来就是覆盖写，留着旧的 runtime 反而快得多。
    # 真要从零重打：手工删掉 dist 目录再跑一次。
    os.makedirs(RUNTIME, exist_ok=True)
    os.makedirs(APP, exist_ok=True)
    os.makedirs(PKGS, exist_ok=True)

    # 1) 嵌入式 Python
    zp = os.path.join(HERE, "runtime", "pyembed.zip")
    if not os.path.isfile(zp):
        raise SystemExit("缺少 runtime/pyembed.zip（嵌入式 Python），先下载了才能打包")
    if os.path.isfile(os.path.join(RUNTIME, "python.exe")):
        print("  嵌入式 Python 已就位，跳过解压")
    else:
        with zipfile.ZipFile(zp) as z:
            z.extractall(RUNTIME)
        print("  嵌入式 Python 解压到 runtime/")

    # 2) 依赖
    for name in NEED_PKG:
        src = os.path.join(SP, name)
        if not os.path.exists(src):
            print("   跳过（本机没有）:", name)
            continue
        n = copy_tree(src, os.path.join(PKGS, name))
        print("   %-18s %5d 个文件" % (name, n))

    for f in NEED_FILE:
        src = os.path.join(SP, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(PKGS, f))
            print("   胶水文件:", f)
        else:
            print("   缺少胶水文件:", f)

    # pywin32 有两个「伪装成 DLL 的扩展模块」：Python 只认 .pyd 后缀
    # （EXTENSION_SUFFIXES 里没有 .dll），而 pywin32 把 pythoncom313.dll
    # 放在 pywin32_system32/ 里、win32ui.pyd 放在 pythonwin/ 里。
    # 直接照搬过去会 import 不到，要改名并挪到 site-packages 下。
    ex = []
    pairs = [
        (os.path.join(SP, "pywin32_system32", "pythoncom313.dll"),
         os.path.join(PKGS, "pythoncom.pyd")),
        (os.path.join(SP, "pythonwin", "win32ui.pyd"),
         os.path.join(PKGS, "win32ui.pyd")),
    ]
    for src, dst in pairs:
        if os.path.isfile(src):
            shutil.copy2(src, dst)
            ex.append(os.path.basename(dst))
        else:
            print("   警告：找不到", src)
    print("   原生扩展改名就位:", ex)

    # 根目录下的原生扩展：pillow_heif 的 .pyd 加上它依赖的 libheif 系列 DLL。
    # 少了这些，iPhone 传来的 HEIC 照片会打不开。
    n = 0
    for f in sorted(os.listdir(SP)):
        if f.lower().endswith((".pyd", ".dll")):
            shutil.copy2(os.path.join(SP, f), os.path.join(PKGS, f))
            n += 1
    print("   根目录原生文件 %d 个" % n)

    # 3) 让嵌入式解释器找得到 site-packages 和源码
    # 注意：._pth 里必须保留「标准库 zip」那一行——3.x 嵌入式版把整个标准库
    # 打包在 python3xx.zip 里，漏了它就连 encodings 都 import 不到。
    zips = [f for f in os.listdir(RUNTIME) if f.endswith(".zip")]
    pth = None
    for f in os.listdir(RUNTIME):
        if f.endswith("._pth"):
            pth = os.path.join(RUNTIME, f)
            break
    if pth:
        # 路径分隔符一律用 os.path.join 生成：源码里手写反斜杠字面量，
        # 经 shell 转义后会变成控制字符（..\app 能变成 ..<BEL>pp）
        lines = list(zips) + [".", os.path.join("..", "app"),
                              os.path.join("Lib", "site-packages"), "import site"]
        with open(pth, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
        print("   已改写", os.path.basename(pth), "->", lines)
    else:
        print("   警告：没找到 ._pth")

    # 4) 源码
    n = 0
    for f in NEED_PY:
        s = os.path.join(HERE, f)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(APP, f))
            n += 1
    print("   源码 %d 个 py" % n)
    # dirs_exist_ok=True：重复打包时目标目录还在，否则 copytree 直接 FileExistsError
    shutil.copytree(os.path.join(HERE, "web"), os.path.join(APP, "web"),
                    ignore=shutil.ignore_patterns(*SKIP_DIR), dirs_exist_ok=True)
    print("   web/ 已复制")

    # 5) 启动脚本（GBK 编码写 bat，中文注释才不乱码）
    # 用 python.exe 而不是 pythonw.exe：这个窗口要一直留着显示网址，
    # 换成无窗口版本用户就不知道该让手机访问哪个地址了。
    bat = r"""@echo off
title 手机打印助手
cd /d "%~dp0app"
set PYTHONIOENCODING=utf-8
"%~dp0runtime\python.exe" server.py
echo.
echo 打印服务已停止。窗口可以关掉了。
pause
"""
    open(os.path.join(DIST, "启动打印助手.bat"), "w", encoding="gbk").write(bat)

    open(os.path.join(DIST, "排错模式.bat"), "w", encoding="gbk").write(bat)

    readme = """手机打印助手 · 便携版
=====================================

【怎么用】
1. 双击「启动打印助手.bat」
2. 命令行窗口会显示一个网址，例如 http://192.168.x.x:8760
3. 手机连同一个 WiFi，浏览器打开那个网址即可
4. 手机和电脑要在同一个局域网；建议给这台电脑设固定 IP

【包括什么】
- 免安装 Python（官方嵌入式版，不写注册表）
- 全部依赖库已在 runtime/Lib/site-packages，离线可用
- 源码在 app/，页面在 app/web/

【常见问题】
- 手机打不开网址：多半是防火墙。以管理员身份运行一次
  mir 名的 bat，或在防火墙里放行本程序。
- 看不到打印机：确认目标打印机已经装好驱动并能打印，
  程序会自动找出真实的打印机（虚拟 PDF 打印机已被自动排除）。
- 微信文件读不到：电脑版微信要保持登录并运行。
- 想看错误详情：运行「排错模式.bat」。

【安全说明】
- 不会联网上传任何内容，所有文件只在本机和局域网内流转。
- 只读微信自己落盘的缓存文件，绝不修改、删除微信原始数据。
- 聊天里直接发送的图片是加密存储的（扫描进程内存才能解，
  本程序不这么做），能取的是本地明文缓存的部分。
"""
    open(os.path.join(DIST, "使用说明.txt"), "w", encoding="utf-8").write(readme)

    total = 0
    for dp, dn, fn in os.walk(DIST):
        dn[:] = [d for d in dn if d not in SKIP_DIR]
        for f in fn:
            total += os.path.getsize(os.path.join(dp, f))
    print()
    print("打包完成 ->", DIST)
    print("总体积 %.1f MB" % (total / 1048576.0))


if __name__ == "__main__":
    main()
