# -*- coding: utf-8 -*-
"""安装辅助：防火墙放行 + 开机自启。需要管理员权限（由 .bat 提权后调用）。"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")
STARTUP = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
LNK = os.path.join(STARTUP, "手机打印服务.lnk")
RULE = "手机打印服务"
PORTS = "8760-8765"

FILTERW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, shell=isinstance(cmd, str),
                       creationflags=FILTERW)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def pythonw():
    cand = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.exists(cand):
        return cand
    return sys.executable


def firewall():
    run('netsh advfirewall firewall delete rule name="%s"' % RULE)
    rc, out = run([
        "netsh", "advfirewall", "firewall", "add", "rule",
        'name=%s' % RULE, "dir=in", "action=allow", "protocol=TCP",
        "localport=%s" % PORTS, "remoteip=localsubnet", "profile=any",
    ])
    return rc == 0, out.strip()[-200:]


def autostart_install():
    try:
        from win32com.client import Dispatch
    except Exception as e:
        return False, str(e)
    try:
        if not os.path.isdir(STARTUP):
            os.makedirs(STARTUP, exist_ok=True)
        ws = Dispatch("WScript.Shell")
        lnk = ws.CreateShortcut(LNK)
        lnk.TargetPath = pythonw()
        lnk.Arguments = '"%s"' % SERVER
        lnk.WorkingDirectory = HERE
        lnk.Description = "手机打印服务（局域网）"
        lnk.Save()
        return True, LNK
    except Exception as e:
        return False, str(e)


def autostart_remove():
    try:
        if os.path.exists(LNK):
            os.remove(LNK)
            return True, "已移除"
        return True, "本来就没有"
    except Exception as e:
        return False, str(e)


def port_busy(port: int) -> bool:
    import socket
    s = socket.socket()
    s.settimeout(0.6)
    busy = s.connect_ex(("127.0.0.1", port)) == 0
    s.close()
    return busy


def start_now(port: int = 8760):
    # 已经在跑就不要重复启动，否则新进程会绑端口失败并静默退出
    if port_busy(port):
        return True, "服务已在运行（端口 %d 已被占用，沿用现有进程）" % port
    try:
        subprocess.Popen([sys.executable, SERVER], cwd=HERE,
                         creationflags=FILTERW | getattr(subprocess, "DETACHED_PROCESS", 0))
        return True, "启动中"
    except Exception as e:
        return False, str(e)


def main():
    print("=" * 56)
    print("  手机打印服务 · 安装")
    print("=" * 56)

    ok, msg = firewall()
    print("[%s] 防火墙放行 TCP %s（仅限本局域网）" % ("OK " if ok else "FAIL", PORTS))
    if msg:
        print("      %s" % msg.replace("\n", " "))

    ok2, msg2 = autostart_install()
    print("[%s] 开机自动启动" % ("OK " if ok2 else "FAIL"))
    if msg2:
        print("      %s" % msg2)

    ok3, msg3 = start_now()
    print("[%s] 立即启动服务" % ("OK " if ok3 else "FAIL"))

    print("-" * 56)
    print("完成。手机连同一个 WiFi 后，用浏览器打开：")
    try:
        sys.path.insert(0, HERE)
        import server
        print("   http://%s:%d" % (server.local_ip(), 8760))
    except Exception:
        print("   http://<电脑IP>:8760")
    print("=" * 56)
    return 0 if (ok and ok2 and ok3) else 1


if __name__ == "__main__":
    sys.exit(main())
