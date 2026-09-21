"""在桌面创建「手机打印助手」快捷方式。

为什么双击就够：以前要开网页得先在浏览器里敲 http://192.168.x.x:8760，
IP 一旦变了就打不开。快捷方式指向一个 bat，它先探一下服务在不在，
不在就顺带把服务拉起来，再打开浏览器。一个图标搞定。

用法：
    python tools/make_shortcuts.py          # 创建（已存在就覆盖）
    python tools/make_shortcuts.py --remove # 删掉自己建的这几个文件
"""

import argparse
import os
import subprocess
import sys

ROOT = r"C:\Users\旭\WorkBuddy\2026-09-17-20-26-42\printserver"
PORT = 8760
BAT = os.path.join(ROOT, "打开打印网站.bat")
LINK_NAME = "手机打印助手.lnk"


def log(s=""):
    print(s)
    sys.stdout.flush()


def _ps(script, timeout=40):
    # 强制 PowerShell 用 UTF-8 输出：回传中文路径（用户名就是中文）时，
    # 默认的 GBK 会让 Python 端解出乱码，看一眼还以为路径写错了。
    script = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + script
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                         capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=timeout)
    if out.returncode != 0 and out.stderr:
        raise RuntimeError(out.stderr.strip()[:300])
    return (out.stdout or "").strip()


def desktop_dir():
    """桌面真实路径。可能被 OneDrive 重定向过，别写死 C:\\Users\\xxx\\Desktop。

    走注册表而不是 PowerShell：PowerShell 回传中文用户名时会按 GBK 解码，
    拿回来就是乱码，路径直接不可用。
    """
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
        val, _ = winreg.QueryValueEx(k, "Desktop")
    d = os.path.expandvars(val)
    if not d or not os.path.isdir(d):
        raise RuntimeError("桌面路径不可用：%r" % d)
    return d


def write_bat():
    # BAT 用 GBK 写、不带 chcp 切页：Windows 控制台默认代码页就是 GBK，
    # 写成 UTF-8 反而会让中文提示变乱码。
    # 注意：这里是普通字符串拼接，不是 %-格式化，%~dp0 只写一个百分号。
    body = "\r\n".join([
        "@echo off",
        "cd /d %~dp0",
        "curl.exe -s -o nul --max-time 2 http://127.0.0.1:%d/health" % PORT,
        "if not errorlevel 1 goto OPEN",
        "echo 打印服务还没开，正在启动...",
        'start "" "%~dp0启动打印服务.bat"',
        "echo 等服务起来...",
        "timeout /t 5 /nobreak >nul",
        ":OPEN",
        "start \"\" http://127.0.0.1:%d" % PORT,
        "",
    ])
    with open(BAT, "w", encoding="gbk", errors="replace", newline="") as f:
        f.write(body)
    log("  已写 %s" % BAT)


def make_link(desktop):
    lnk = os.path.join(desktop, LINK_NAME)
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$s = $ws.CreateShortcut('%s'); "
        "$s.TargetPath = '%s'; "
        "$s.WorkingDirectory = '%s'; "
        "$s.Description = '打开手机打印助手'; "
        "$s.IconLocation = '%%SystemRoot%%\\System32\\imageres.dll,168'; "
        "$s.Save()"
        % (lnk.replace("'", "''"), BAT, ROOT)
    )
    _ps(ps)
    log("  已建桌面快捷方式 %s" % lnk)


def verify_link(desktop):
    """把快捷方式指向的东西读回来核对一遍：目标错了我看不出，双击才发现就晚了。"""
    lnk = os.path.join(desktop, LINK_NAME)
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$s = $ws.CreateShortcut('%s'); "
        "Write-Output ('target=' + $s.TargetPath); "
        "Write-Output ('dir=' + $s.WorkingDirectory); "
        "Write-Output ('icon=' + $s.IconLocation)"
        % lnk.replace("'", "''")
    )
    log("=== 快捷方式读回来 ===")
    for line in _ps(ps).splitlines():
        log("  " + line.strip())
    if os.path.isfile(BAT):
        log("=== bat 内容（按写入时的编码读回） ===")
        try:
            for line in open(BAT, encoding="gbk").read().splitlines():
                log("  " + line)
        except Exception as e:
            log("  读回失败：%s" % e)
    else:
        log("=== bat 不在：%s" % BAT)


def main():
    ap = argparse.ArgumentParser(description="创建桌面快捷方式")
    ap.add_argument("--remove", action="store_true", help="删掉快捷方式和那个 bat")
    ap.add_argument("--verify", action="store_true", help="只读回来看，不创建")
    args = ap.parse_args()

    try:
        d = desktop_dir()
    except Exception as e:
        log("❌ %s" % e)
        return

    lnk = os.path.join(d, LINK_NAME)

    if args.verify:
        verify_link(d)
        return

    if args.remove:
        for p in (lnk, BAT):
            if os.path.exists(p):
                os.remove(p)
                log("  已删 %s" % p)
            else:
                log("  本来就不存在：%s" % p)
        return

    log("桌面目录：%s" % d)
    write_bat()
    try:
        make_link(d)
    except Exception as e:
        log("❌ 快捷方式没建成：%s" % e)
        return
    log("\n双击桌面上的「手机打印助手」就能打开。服务没开时它会自动帮你开。")


if __name__ == "__main__":
    main()
