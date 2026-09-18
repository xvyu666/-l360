"""重启手机打印服务。

为什么要有这个脚本：
    打印服务是个长期常驻进程，改完 server.py / inbox.py 之后，
    老进程内存里还是旧代码，新加的接口一律回 404
    （表现：手机中转 APP 传文件报"电脑返回 404"，网页面板看不到文件）。
    以前靠手工"关黑窗口再双击 bat"，容易漏，也有凭用户名/端口误杀别的进程的风险。

安全策略：
    只结束「命令行指向本项目 server.py」的那个进程。netstat 找到 PID 之后，
    用 WMI 反查命令行做二次确认，不匹配就原样退出，什么都不做。

用法：
    python tools/restart_service.py            # 重启
    python tools/restart_service.py --stop     # 只停
    python tools/restart_service.py --probe    # 只探测，不动进程
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

ROOT = r"C:\Users\旭\WorkBuddy\2026-09-17-20-26-42\printserver"
VENVPY = r"C:\Users\旭\.workbuddy\binaries\python\envs\printserver\Scripts\python.exe"
TARGET_PORT = 8760
SERVER_PY = os.path.join(ROOT, "server.py")

# 最新一版代码该支持的功能，用来判断跑着的进程是不是旧的
WANT_FEATURES = ["inbox", "notes", "wechat"]

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def log(s=""):
    print(s)
    sys.stdout.flush()


def _ps(script, timeout=25):
    """跑一段 PowerShell 拿结果。本机 PowerShell 工具不回显输出，所以用「跑+读」的办法。"""
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                         capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=timeout)
    return (out.stdout or "").strip()


def listening_pid(port):
    """监听该端口的 PID，没有就 None。"""
    s = ("(Get-NetTCPConnection -LocalPort %d -State Listen "
         "-ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess" % port)
    v = _ps(s)
    return int(v) if v.isdigit() else None


def pid_cmdline(pid):
    s = "(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\").CommandLine" % pid
    return _ps(s)


def is_our_server(pid):
    """PID 是不是本项目起的打印服务。"""
    cmd = pid_cmdline(pid)
    if not cmd:
        return False, ""
    low = cmd.lower()
    return ("server.py" in low and "printserver" in low), cmd


def check(port, timeout=6):
    """探一下关键接口，返回 dict path->code。"""
    res = {}
    for path in ["/api/info", "/api/inbox/list", "/api/notes/list"]:
        code = _code(port, path, timeout)
        res[path] = code
    return res


def _code(port, path, timeout):
    url = "http://127.0.0.1:%d%s" % (port, path)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return type(e).__name__


def features(port, timeout=6):
    """读 /api/info 的 features 数组 —— 服务端自己报告它支持哪些功能。"""
    url = "http://127.0.0.1:%d/api/info" % port
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read(4000).decode("utf-8", "replace")).get("features", [])
    except Exception:
        return None


def wait_port_free(port, limit=40):
    for _ in range(limit):
        if listening_pid(port) is None:
            return True
        time.sleep(0.4)
    return False


def wait_health(port, limit=60):
    for _ in range(limit):
        if _code(port, "/health", 2) == 200:
            return True
        time.sleep(0.5)
    return False


def probe(port):
    log("=== 端口 %d 探测 ===" % port)
    pid = listening_pid(port)
    log("  监听进程：%s" % (pid or "无"))
    if pid:
        ok, cmd = is_our_server(pid)
        log("  是本项目打印服务：%s" % ("是" if ok else "否"))
        if cmd:
            log("  命令行：%s" % cmd[:160])
        started = _ps("(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\").CreationDate"
                      % pid)
        if started:
            log("  启动时间：%s" % started)

    log("  接口状态：")
    for k, v in check(port).items():
        flag = "OK" if v == 200 else ("<<< 404：进程用的是旧代码" if v == 404 else "异常")
        log("    %-20s -> %-12s %s" % (k, v, flag))

    fts = features(port)
    if fts is None:
        log("  服务端特性：读不到（服务没起来）")
    else:
        missing = [x for x in WANT_FEATURES if x not in fts]
        log("  服务端特性：%s" % (", ".join(fts) or "（空）"))
        if missing:
            log("    !! 缺少 %s —— 这个进程加载的是改之前的 server.py，"
                "必须重启才会生效。" % ", ".join(missing))
        else:
            log("    功能齐全，进程是最新代码。")


def stop(port):
    pid = listening_pid(port)
    if pid is None:
        log("端口 %d 上没有服务在跑。" % port)
        return True
    ok, cmd = is_our_server(pid)
    if not ok:
        log("端口 %d 上的进程不是本项目打印服务，拒绝操作。" % port)
        log("  命令行：%s" % cmd[:200])
        return False
    log("停止旧服务 PID=%s（%s）" % (pid, time.strftime("%H:%M:%S")))
    subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                   capture_output=True, text=True)
    if not wait_port_free(port):
        log("端口没释放，放弃。")
        return False
    log("  端口已释放。")
    return True


def start_detached(port):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    p = subprocess.Popen(
        [VENVPY, "-u", "server.py"], cwd=ROOT, env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True)
    log("新服务已拉起 PID=%s（后台常驻，无窗口）" % p.pid)
    return p.pid


def main():
    ap = argparse.ArgumentParser(description="重启手机打印服务")
    ap.add_argument("--stop", action="store_true", help="只停不启")
    ap.add_argument("--probe", action="store_true", help="只探测")
    args = ap.parse_args()

    if args.probe:
        probe(TARGET_PORT)
        return

    if not os.path.exists(SERVER_PY):
        log("找不到 server.py：%s" % SERVER_PY)
        return

    log("重启前：")
    probe(TARGET_PORT)
    log()

    if not stop(TARGET_PORT):
        return
    if args.stop:
        return

    time.sleep(0.6)
    start_detached(TARGET_PORT)
    log()
    if not wait_health(TARGET_PORT):
        log("服务没起来，检查 %s 目录的 runtime/server.log" % ROOT)
        return
    log("服务已就绪。重启后：")
    probe(TARGET_PORT)


if __name__ == "__main__":
    main()
