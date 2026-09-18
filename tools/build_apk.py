#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在本机编译安卓中转 APK —— 不装 Android Studio。

所有工具都下载到纯 ASCII 目录 C:\\android-kit 下（JDK / Android SDK / Gradle），
不写注册表、不改系统 PATH。用完可以直接把整个目录删掉。

    python tools/build_apk.py            一键：装环境 + 编译 debug 和 release
    python tools/build_apk.py --debug    只编译 debug
    python tools/build_apk.py --check    只看工具链状态，不触发编译

为什么工具装在 C:\\android-kit 而不是用户目录：
    本机用户名是中文，Android SDK / Gradle 对含非 ASCII 的路径并不友好，
    踩过的人都知道那些报错有多难读。统一放 ASCII 路径最省心。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

KIT = Path(r"C:\android-kit")
JDK = KIT / "jdk17"
SDK = KIT / "sdk"
GRADLE = KIT / "gradle-8.7"

JDK_URL = ("https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/"
           "jdk/hotspot/normal/eclipse")
GRADLE_URL = "https://services.gradle.org/distributions/gradle-8.7-bin.zip"
REPO_XML = "https://dl.google.com/android/repository/repository2-3.xml"

# 预先写好 SDK license 的 hash，免得 sdkmanager 交互式询问（无人值守必做）
LICENSES = {
    "android-sdk-license": [
        "8933bad161af4178b1185d1a37fbf41ea5269c55",
        "d56f5187479451eabf01fb78af6dfcb131a6481e",
        "24333f8a63b6825ea76c3a7ad0a8b2b0c2b47f5c",
    ],
    "android-sdk-preview-license": [
        "84831b9409646a918e30573bab4c9c91346d8abd",
    ],
}


def clean_proxy():
    """本机 git bash 会注入指向已失效端口的代理，Python 的 urllib 会去读它。"""
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(k, None)


def log(msg):
    print(msg, flush=True)


def download(url, dest, label):
    if dest.exists() and dest.stat().st_size > 0:
        log("  已有下载，跳过：%s" % dest.name)
        return dest
    clean_proxy()
    log("  下载 %s ..." % label)
    req = urllib.request.Request(url, headers={"User-Agent": "printserver-build"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            mb = done / 1048576
            if total:
                sys.stdout.write("\r  %6.1f / %6.1f MB" % (mb, total / 1048576))
            else:
                sys.stdout.write("\r  %6.1f MB" % mb)
            sys.stdout.flush()
        print()
    tmp.replace(dest)
    return dest


def extract_first(zip_path, dest):
    """解压，并把压缩包里唯一的顶层目录摊平到 dest。"""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        root = names[0].split("/")[0]
        z.extractall(dest.parent / "_unpack")
    src = dest.parent / "_unpack" / root
    if not src.exists():
        raise RuntimeError("解压后没找到预期的顶层目录：%s" % root)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(src), str(dest))
    shutil.rmtree(str(dest.parent / "_unpack"), ignore_errors=True)
    return dest


def ensure_jdk():
    if (JDK / "bin" / "java.exe").exists():
        log("[JDK] 已就绪")
        return JDK
    KIT.mkdir(parents=True, exist_ok=True)
    z = download(JDK_URL, KIT / "jdk17.zip", "OpenJDK 17 (约 190MB)")
    log("  解压 JDK ...")
    extract_first(z, JDK)
    if not (JDK / "bin" / "java.exe").exists():
        raise RuntimeError("JDK 解压后找不到 bin\\java.exe")
    log("[JDK] 完成")
    return JDK


def find_cmdline_tools_url():
    """从官方 repository 清单里挑最新的 Windows 版 cmdline-tools。"""
    clean_proxy()
    req = urllib.request.Request(REPO_XML, headers={"User-Agent": "printserver-build"})
    with urllib.request.urlopen(req, timeout=60) as r:
        xml = r.read().decode("utf-8", "replace")
    hits = re.findall(r"commandlinetools-win-(\d+)_latest\.zip", xml)
    if not hits:
        return None
    ver = max(hits, key=int)
    return "https://dl.google.com/android/repository/commandlinetools-win-%s_latest.zip" % ver, ver


def ensure_sdk(jdk):
    sm = SDK / "cmdline-tools" / "latest" / "bin" / "sdkmanager.bat"
    if sm.exists():
        log("[SDK] cmdline-tools 已就绪")
    else:
        found = find_cmdline_tools_url()
        if not found:
            raise RuntimeError("没能解析出 cmdline-tools 的下载地址")
        url, ver = found
        log("[SDK] 使用 cmdline-tools %s" % ver)
        z = download(url, KIT / "cmdline-tools.zip", "Android cmdline-tools")
        unpack = KIT / "_ct"
        if unpack.exists():
            shutil.rmtree(unpack)
        with zipfile.ZipFile(z) as f:
            f.extractall(unpack)
        target = SDK / "cmdline-tools" / "latest"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(unpack / "cmdline-tools"), str(target))
        shutil.rmtree(str(unpack), ignore_errors=True)

    lic = SDK / "licenses"
    lic.mkdir(parents=True, exist_ok=True)
    for name, hashes in LICENSES.items():
        (lic / name).write_text("\n".join(hashes) + "\n", encoding="utf-8")

    need = ["platform-tools", "platforms;android-34", "build-tools;34.0.0"]
    missing = [p for p in need if not (SDK / p.replace(";", os.sep)).exists()]
    if missing:
        log("[SDK] 安装组件：%s" % ", ".join(missing))
        run([str(sm), "--sdk_root=" + str(SDK)] + missing, "[sdkmanager] ", jdk)
    else:
        log("[SDK] 组件齐备")
    return SDK


def ensure_gradle():
    bat = GRADLE / "bin" / "gradle.bat"
    if bat.exists():
        log("[Gradle] 已就绪")
        return bat
    z = download(GRADLE_URL, KIT / "gradle-8.7.zip", "Gradle 8.7 (约 130MB)")
    log("  解压 Gradle ...")
    extract_first(z, GRADLE)
    log("[Gradle] 完成")
    return bat


def env_for(jdk, sdk):
    e = dict(os.environ)
    e["JAVA_HOME"] = str(jdk)
    e["ANDROID_HOME"] = str(sdk)
    e["ANDROID_SDK_ROOT"] = str(sdk)
    e["PATH"] = str(jdk / "bin") + os.pathsep + e.get("PATH", "")
    clean_proxy_for(e)
    return e


def clean_proxy_for(e):
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        e.pop(k, None)


def run(cmd, tag, jdk=None, cwd=None):
    e = env_for(jdk, SDK) if jdk else None
    # shell=True 下必须自己加引号：cmd.exe 把空格**和分号**都当分隔符，
    # 所以 `platforms;android-34` 不加引号会被拆成两个词，
    # sdkmanager 于是回一堆 "Package platforms not found" 还退 0，非常容易白忙。
    cmdline = " ".join('"%s"' % c for c in cmd)
    p = subprocess.run(cmdline, env=e, cwd=cwd, shell=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace")
    out = p.stdout or ""
    tail = out[-8000:]
    print(tag + "exit=%d" % p.returncode, flush=True)
    if tail:
        print("\n".join("  | " + l for l in tail.splitlines()[-120:]), flush=True)
    if p.returncode != 0:
        raise SystemExit("命令失败：%s" % (cmd[0] if isinstance(cmd, list) else cmd))


def check():
    rows = [
        ("JDK 17", (JDK / "bin" / "java.exe").exists(), JDK),
        ("Android SDK", (SDK / "platforms" / "android-34").exists(), SDK),
        ("Build-tools 34", (SDK / "build-tools" / "34.0.0").exists(), SDK / "build-tools"),
        ("cmdline-tools", (SDK / "cmdline-tools" / "latest" / "bin" / "sdkmanager.bat").exists(), SDK),
        ("Gradle 8.7", (GRADLE / "bin" / "gradle.bat").exists(), GRADLE),
    ]
    for name, ok, path in rows:
        log("%-18s %s  %s" % (name, "OK " if ok else "缺失", path))
    return all(r[1] for r in rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只看工具链状态")
    ap.add_argument("--debug", action="store_true", help="只编译 debug 包")
    ap.add_argument("--project", default=None, help="android-relay 目录，默认自动定位")
    args = ap.parse_args()

    if args.check:
        sys.exit(0 if check() else 1)

    here = Path(__file__).resolve().parent.parent
    proj = Path(args.project) if args.project else (here / "android-relay")
    if not (proj / "app" / "build.gradle").exists():
        raise SystemExit("找不到安卓工程：%s" % proj)

    log("=== 1/4 工具链 ===")
    jdk = ensure_jdk()
    log("=== 2/4 Android SDK ===")
    sdk = ensure_sdk(jdk)
    log("=== 3/4 Gradle ===")
    gradle = ensure_gradle()

    # 中文用户名路径会让 AGP 直接罢工（"project path contains non-ASCII characters"），
    # 所以把工程复制到 ASCII 目录再编，编完把 APK 拷回来。
    build_src = KIT / "src" / "android-relay"
    log("  复制到 ASCII 构建目录：%s" % build_src)
    # 用 dirs_exist_ok 覆盖式复制，不做 rmtree：
    #   1) 删整目录容易触发环境的批量删除保护
    #   2) 留下 app/build 和 .gradle 缓存能让下一次编译快得多
    # ignore 里排除 build/.gradle，就是为了保住这些缓存不被源目录的同名空/旧内容顶掉
    shutil.copytree(str(proj), str(build_src), dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("build", ".gradle", ".idea"))
    (build_src / "local.properties").write_text(
        "sdk.dir=" + str(SDK).replace("\\", "/") + "\n", encoding="utf-8")

    # 多个 task 必须是列表里的独立参数，拼成一个字符串会当成单个任务名去找
    tasks = ["assembleDebug"] if args.debug else ["assembleDebug", "assembleRelease"]
    log("=== 4/4 编译 APK ===")
    log("源工程：%s" % proj)
    run([str(gradle), "-p", str(build_src)] + tasks + ["--no-daemon", "--console=plain"],
        "[gradle] ", jdk)

    # 产物拷回原工程，路径和云端 workflow 保持一致
    for variant in ("debug", "release"):
        src = build_src / "app" / "build" / "outputs" / "apk" / variant
        dst = proj / "app" / "build" / "outputs" / "apk" / variant
        if src.exists():
            dst.mkdir(parents=True, exist_ok=True)
            for f in src.glob("*.apk"):
                shutil.copy2(str(f), str(dst / f.name))

    debug_apk = proj / "app/build/outputs/apk/debug/app-debug.apk"
    release_apk = proj / "app/build/outputs/apk/release/app-release.apk"
    log("\n=== 产物 ===")
    for p in (debug_apk, release_apk):
        if p.exists():
            log("  %-16s %s  (%.0f KB)" % (p.name, p, p.stat().st_size / 1024))
        elif p.name.startswith("app-debug"):
            log("  %s 未生成" % p.name)


if __name__ == "__main__":
    main()
