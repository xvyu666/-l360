# -*- coding: utf-8 -*-
"""从微信 PC 端读取聊天里收到的文件，做成「微信自助打印」。

微信把聊天中收发的文件原样落在本地数据目录里（带真实文件名），
我们只读这个目录，不碰聊天数据库、不注入微信进程、不改任何微信文件。

微信 4.x： <数据目录>/xwechat_files/wxid_xxx_xxxx/msg/file/YYYY-MM/文件名
微信 3.x： <数据目录>/WeChat Files/wxid_xxx/FileStorage/File/YYYY-MM/文件名

会话结构：cache/<月>/Message/<会话hash>/Thumb/ 下是聊天图片的明文缩略图，
其中「会话 hash = md5(会话 wxid)」——所以文件传输助手就是 md5("filehelper")，可精确定位。

聊天里直接发的「图片」原图在 msg/attach 里存的是 V2 加密 .dat（已实测：
全文件 AES 加密，无明文区，离线不可解，见 WXIMG_NOTE），所以不去碰它。
能打的是这些明文来源：
  1. chat     缓存缩略图      —— 收到消息自动生成，覆盖面最广
  2. favorite 收藏的高清图    —— 质量最好的一批
  3. migrate  迁移出来的图    —— 明文 png
另外「以文件形式」收发的图片和普通文件一样落在 msg/file，由 scan() 覆盖。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time

from PIL import Image

import renderer as RD

_SCAN_TTL = 12          # 扫描结果缓存秒数（列表页刷新够快，又不至于每次全盘 stat）
_MAX_FILES = 400        # 最多返回多少个文件
_lock = threading.RLock()
_cache = {"ts": 0.0, "key": None, "files": []}
_index = {}             # fid -> 绝对路径（路径不变，重启后也能对上）
_img_index = {}         # fid -> 绝对路径，专门给扫描出来的图片用
_img_cache = {"ts": 0.0, "key": None, "items": []}
_IMAGE_TTL = 15         # 图片列表缓存秒数（要开 PIL 读尺寸，别每次都算）
_MIN_EDGE = 240         # 默认过滤掉的最大边下限——头像/表情只有 150px 上下
_MAX_IMAGES = 300
_roots_cache = {"ts": 0.0, "value": []}


# ---------------------------------------------------------------- 定位数据目录


def _reg_dirs():
    """微信把自定义的数据保存路径写在注册表里（例如有人会改到别的盘）。"""
    out = []
    try:
        import winreg
    except ImportError:
        return out
    for sub in (r"Software\Tencent\Weixin", r"Software\Tencent\WeChat"):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            continue
        i = 0
        while True:
            try:
                _n, val, _t = winreg.EnumValue(key, i)
            except OSError:
                break
            i += 1
            if isinstance(val, str) and val and os.path.isdir(val):
                out.append(val)
    return out


def _candidate_bases():
    home = os.path.expanduser("~")
    cands = []
    for p in _reg_dirs():
        cands.append(p)
        cands.append(os.path.join(os.path.dirname(p.rstrip("\\/")), "xwechat_files"))
        cands.append(os.path.join(p, "xwechat_files"))
        cands.append(os.path.join(p, "WeChat Files"))
    for p in (
        os.path.join(home, "Documents", "xwechat_files"),
        os.path.join(home, "Documents", "WeChat Files"),
        os.path.join(home, "WeChat Files"),
        os.path.join(home, "xwechat_files"),
    ):
        cands.append(p)
    for drv in "CDEFGHIJ":
        root = "%s:\\" % drv
        if not os.path.isdir(root):
            continue
        cands.append(os.path.join(root, "xwechat_files"))
        cands.append(os.path.join(root, "WeChat Files"))
    seen, out = set(), []
    for c in cands:
        try:
            c = os.path.abspath(c)
        except Exception:
            continue
        if c in seen or not os.path.isdir(c):
            continue
        seen.add(c)
        out.append(c)
    return out


def _accounts_in(base, depth=0):
    """在一个候选根目录下找出「账号目录 + 文件落地目录」配对。"""
    out = []
    try:
        names = os.listdir(base)
    except OSError:
        return out
    for n in names:
        p = os.path.join(base, n)
        if not os.path.isdir(p):
            continue
        if n in ("All Users", "all_users", "Applet", "WMPF", "Backup", "BackupFiles"):
            continue
        f4 = os.path.join(p, "msg", "file")
        f3 = os.path.join(p, "FileStorage", "File")
        if os.path.isdir(f4):
            out.append((p, f4))
        elif os.path.isdir(f3):
            out.append((p, f3))
    # 3.x 的自定义路径是 <路径>/WeChat Files/wxid_xxx，再往下找一层
    if depth < 2:
        for sub in ("WeChat Files", "xwechat_files"):
            sp = os.path.join(base, sub)
            if os.path.isdir(sp):
                out += _accounts_in(sp, depth + 1)
    return out


def find_roots(force=False):
    """返回 [(账号目录, 文件目录, 微信版本), ...]"""
    now = time.time()
    if not force and _roots_cache["value"] and now - _roots_cache["ts"] < 120:
        return _roots_cache["value"]
    out, seen = [], set()
    for base in _candidate_bases():
        for acc, fdir in _accounts_in(base):
            if acc in seen:
                continue
            seen.add(acc)
            ver = "4.x" if fdir.endswith("msg\\file") or fdir.endswith("msg/file") else "3.x"
            out.append((acc, fdir, ver))
    _roots_cache["ts"] = now
    _roots_cache["value"] = out
    return out


def wechat_running():
    """微信 PC 端是否在运行——没登录/没运行的话，新文件不会落盘。"""
    # 注意：两个 /FI 是「与」关系，写在一起永远匹配不到，必须分开查。
    # tasklist 在中文 Windows 上吐的是 GBK，按 utf-8 解码会直接抛异常。
    for exe in ("Weixin.exe", "WeChat.exe"):
        try:
            proc = subprocess.run(
                ["tasklist", "/NH", "/FI", "IMAGENAME eq " + exe],
                capture_output=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if exe.encode("ascii") in (proc.stdout or b""):
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------- 扫描文件


def fid_of(path: str) -> str:
    return hashlib.md5(os.path.abspath(path).encode("utf-8")).hexdigest()[:16]


def _kind(name: str) -> str:
    return RD.classify(name)


def scan(hours=72, limit=120, keyword="", force=False, since=0.0):
    """扫描所有账号目录里的文件。

    hours=0 表示不限时间。since 是 Unix 时间戳，只返回 mtime >= since 的文件，
    用来做「收件箱」：只列自上次收集以来新到的那些。返回按修改时间倒序。
    """
    hours = max(0, int(hours or 0))
    limit = max(1, min(int(limit or 120), _MAX_FILES))
    keyword = (keyword or "").strip().lower()
    try:
        since = float(since or 0)
    except (TypeError, ValueError):
        since = 0.0
    key = (hours, limit, keyword, since)
    now = time.time()
    # 带 since 的请求是靠 mtime 卡边界的，缓存会把「刚落盘的新文件」挡在门外，
    # 而收件箱恰恰就是要实时看到新到的东西 —— 所以一律不走缓存。
    if since > 0:
        force = True

    with _lock:
        if not force and _cache["files"] and _cache["key"] == key and now - _cache["ts"] < _SCAN_TTL:
            return _cache["files"]

    cutoff = now - hours * 3600.0 if hours > 0 else 0.0
    if since > cutoff:
        cutoff = since
    found = []
    for _acc, fdir, _ver in find_roots():
        for dp, _dn, fnames in os.walk(fdir):
            for f in fnames:
                if f.startswith("~$") or f.endswith(".tmp"):
                    continue
                kind = _kind(f)
                if kind == "unknown":
                    continue
                fp = os.path.join(dp, f)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                if cutoff and st.st_mtime < cutoff:
                    continue
                if keyword and keyword not in f.lower():
                    continue
                found.append({
                    "id": fid_of(fp),
                    "name": f,
                    "path": fp,
                    "ext": os.path.splitext(f)[1].lower().lstrip("."),
                    "kind": kind,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                })
    found.sort(key=lambda x: x["mtime"], reverse=True)
    found = found[:limit]

    with _lock:
        _index.clear()
        for it in found:
            _index[it["id"]] = it["path"]
        _cache.update({"ts": now, "key": key, "files": found})
    return found


def resolve(fid: str):
    """fid 反查真实路径；缓存没有就重新扫一遍全量（不限时间）再找。"""
    with _lock:
        p = _index.get(fid) or _img_index.get(fid)
    if p:
        return p if os.path.isfile(p) else None
    for it in scan(hours=0, limit=_MAX_FILES, force=True):
        if it["id"] == fid:
            return it["path"]
    # 图片的 fine-id 存在另一个索引里，也得给一次机会
    for it in scan_images(hours=0, limit=_MAX_IMAGES, force=True):
        if it["id"] == fid:
            return it["path"]
    return None


def public(it: dict, now=None):
    now = now or time.time()
    age = max(0.0, now - it["mtime"])
    return {
        "id": it["id"],
        "name": it["name"],
        "ext": it["ext"],
        "kind": it["kind"],
        "size": it["size"],
        "mtime": it["mtime"],
        "ageSec": int(age),
        "fresh": age < 600,          # 10 分钟内的算「刚到」
        "time": time.strftime("%m-%d %H:%M", time.localtime(it["mtime"])),
    }


def status():
    roots = find_roots()
    infos = []
    for acc, fdir, ver in roots:
        n = 0
        try:
            for _dp, _dn, fn in os.walk(fdir):
                n += len(fn)
        except OSError:
            pass
        infos.append({"account": os.path.basename(acc), "dir": fdir,
                      "version": ver, "files": n})
    return {
        "ok": bool(roots),
        "running": wechat_running(),
        "roots": infos,
    }


# ================================================================ 图片

# 已知 wxid -> 中文名。会话目录名就是 md5(wxid)，所以这几个能直接反查出来。
_SPECIAL_SESSIONS = {
    "filehelper": "文件传输助手",
    "floatbottle": "漂流瓶",
    "qmessage": "QQ 离线消息",
    "medianote": "语音记事本",
}
_session_label_cache = {}


def session_id(wxid: str) -> str:
    """会话 wxid -> 缓存目录名（实测就是 md5 的小写十六进制）。"""
    return hashlib.md5((wxid or "").encode("utf-8")).hexdigest()


FILEHELPER_ID = session_id("filehelper")


def session_label(h: str):
    """会话目录名 -> 可读名字；认不出来返回 None（前端显示 hash 前几位）。"""
    if not h:
        return None
    if h in _session_label_cache:
        return _session_label_cache[h]
    name = None
    for wxid, label in _SPECIAL_SESSIONS.items():
        if session_id(wxid) == h:
            name = label
            break
    _session_label_cache[h] = name
    return name


def _image_dirs():
    """返回 [(目录, 来源标记)]，只保留 4.x 账号下真实存在的那些。"""
    out = []
    for acc, _fdir, ver in find_roots():
        if ver != "4.x":
            continue
        for sub, tag in (
            (("cache",), "chat"),
            (("business", "favorite", "temp", "NoteCache"), "favorite"),
            (("business", "migrate", "local", "p"), "migrate"),
        ):
            d = os.path.join(acc, *sub)
            if os.path.isdir(d):
                out.append((d, tag))
    return out


def _thumb_ts(fname: str):
    """从 123_1789663446_thumb.jpg 里取出消息时间戳。

    文件名里的时间是「消息发出时间」，比文件 mtime 更能反映用户真实关心的
    「什么时候收到的」——所以排序和筛选都以它为准，拿不到才退回 mtime。
    """
    seg = (fname or "").split("_")
    if len(seg) >= 2 and seg[1].isdigit():
        t = int(seg[1])
        if 946684800 < t < 4102444800:      # 2000-01-01 ~ 2099，过滤掉脏数据
            return float(t)
    return None


def _probe_size(path: str):
    """取图片像素尺寸。Image.open 只读文件头，不解码像素，很快。"""
    try:
        with Image.open(path) as im:
            return int(im.size[0]), int(im.size[1])
    except Exception:
        return None


def _dedup_key(path: str, size: int):
    """按「大小 + 头部内容」去重——收藏缓存里同一张图常常存了两遍。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16384)
    except OSError:
        return None
    return hashlib.md5(head).hexdigest() + str(size)


def scan_images(hours=72, limit=120, session="", since=0.0, min_edge=_MIN_EDGE,
                force=False):
    """扫描微信本地的明文图片。

    hours/since 按「消息时间」卡边界（聊天缩略图文件名里带时间戳）；
    session 传会话目录名可只看某一个会话（文件传输助手用 md5("filehelper")）；
    min_edge 过滤掉太小的图——头像、表情只有一百多像素，混进来没法打。
    """
    hours = max(0, int(hours or 0))
    limit = max(1, min(int(limit or 120), _MAX_IMAGES))
    session = (session or "").strip().lower()
    min_edge = max(0, int(min_edge or 0))
    try:
        since = float(since or 0)
    except (TypeError, ValueError):
        since = 0.0

    key = (hours, limit, session, since, min_edge)
    now = time.time()
    if since > 0:
        # 跟收件箱同理：带 since 就是要实时，缓存会把刚到的图挡在门外
        force = True

    with _lock:
        if not force and _img_cache["items"] and _img_cache["key"] == key \
                and now - _img_cache["ts"] < _IMAGE_TTL:
            return _img_cache["items"]

    cutoff = max(now - hours * 3600.0 if hours > 0 else 0.0, since)
    seen, found = set(), []

    for root, tag in _image_dirs():
        if tag == "chat":
            # 布局：cache/<月>/Message/<会话hash>/Thumb/<seq>_<ts>_thumb.jpg
            try:
                months = sorted(os.listdir(root), reverse=True)
            except OSError:
                continue
            for month in months:
                mdir = os.path.join(root, month, "Message")
                if not os.path.isdir(mdir):
                    continue
                try:
                    hashes = os.listdir(mdir)
                except OSError:
                    continue
                for h in hashes:
                    if session and h != session:
                        continue
                    tdir = os.path.join(mdir, h, "Thumb")
                    if not os.path.isdir(tdir):
                        continue
                    try:
                        files = os.listdir(tdir)
                    except OSError:
                        continue
                    for f in files:
                        if not f.lower().endswith((".jpg", ".jpeg", ".png")):
                            continue
                        fp = os.path.join(tdir, f)
                        try:
                            st = os.stat(fp)
                        except OSError:
                            continue
                        ts = _thumb_ts(f) or st.st_mtime
                        if cutoff and ts < cutoff:
                            continue
                        dk = _dedup_key(fp, st.st_size)
                        if dk and dk in seen:
                            continue
                        size = _probe_size(fp)
                        if not size:
                            continue
                        if max(size) < min_edge:
                            continue
                        if dk:
                            seen.add(dk)
                        found.append({
                            "id": fid_of(fp), "path": fp, "kind": "image",
                            "size": st.st_size, "mtime": st.st_mtime, "ts": ts,
                            "session": h, "source": tag, "w": size[0], "h": size[1],
                        })
        else:
            # 布局：普通目录下的文件，扩展名可有可无，一律看内容识别
            if session:
                # 收藏和迁移出来的图不属于任何会话，按会话筛选时要排除掉，
                # 否则会把无关图片算进「某个会话」里。
                continue
            for dp, _dn, fnames in os.walk(root):
                for f in fnames:
                    fp = os.path.join(dp, f)
                    try:
                        st = os.stat(fp)
                    except OSError:
                        continue
                    if cutoff and max(st.st_mtime, 0) < cutoff:
                        continue
                    if not _head_is_image(fp):
                        continue
                    dk = _dedup_key(fp, st.st_size)
                    if dk and dk in seen:
                        continue
                    size = _probe_size(fp)
                    if not size or max(size) < min_edge:
                        continue
                    if dk:
                        seen.add(dk)
                    found.append({
                        "id": fid_of(fp), "path": fp, "kind": "image",
                        "size": st.st_size, "mtime": st.st_mtime,
                        "ts": st.st_mtime, "session": None, "source": tag,
                        "w": size[0], "h": size[1],
                    })

    found.sort(key=lambda x: x["ts"], reverse=True)
    found = found[:limit]

    with _lock:
        _img_index.clear()
        for it in found:
            _img_index[it["id"]] = it["path"]
        _img_cache.update({"ts": now, "key": key, "items": found})
    return found


def _head_is_image(path: str) -> bool:
    """按文件头判断是不是图片——收藏缓存里的图常常连扩展名都没有。"""
    try:
        with open(path, "rb") as fh:
            h = fh.read(12)
    except OSError:
        return False
    return (h[:2] == b"\xff\xd8" or h[:4] == b"\x89PNG" or h[:4] == b"GIF8"
            or (h[:4] == b"RIFF" and h[8:12] == b"WEBP")
            or h[:2] == b"BM")


def image_name(fid: str, path: str = "") -> str:
    """给图片起个能看懂的名字：会话 + 时间 + 像素。

    缓存里的图文件名是一串 hash（收藏的那些连扩展名都没有），直接拿给用户
    根本认不出是哪张，所以导入时要重命名成「文件传输助手_0918-1532_1080x1440.jpg」。
    """
    it = image_info(fid)
    if it:
        label = session_label(it.get("session") or "")
        if label:
            who = label
        elif it.get("session"):
            who = "会话" + (it["session"] or "")[:6]
        elif it.get("source") == "favorite":
            who = "微信收藏"
        else:
            who = "微信图片"
        stamp = time.strftime("%m%d-%H%M", time.localtime(it.get("ts") or it["mtime"]))
        return "%s_%s_%dx%d.jpg" % (who, stamp, it["w"], it["h"])
    # 索引没了（比如服务重启后首次点击）就现场算一个
    base = os.path.basename(path or "")
    size = _probe_size(path) if path else None
    if size:
        return "%s_%dx%d.jpg" % (os.path.splitext(base)[0][:24], size[0], size[1])
    return base or "微信图片.jpg"


def is_image(fid: str) -> bool:
    """这个 id 是不是扫描出来的图片（图片常常没有扩展名，classify 认不出）。"""
    with _lock:
        return fid in _img_index


def image_info(fid: str):
    with _lock:
        return next((i for i in _img_cache["items"] if i["id"] == fid), None)


def public_image(it: dict, now=None):
    now = now or time.time()
    ts = it.get("ts") or it["mtime"]
    age = max(0.0, now - ts)
    w, h = it["w"], it["h"]
    dpi = int(round(_fit_dpi(w, h, 210.0, 297.0)))
    sug, sug_dpi = suggest_paper(w, h)
    return {
        "id": it["id"],
        "name": image_name(it["id"], it["path"]),
        "ext": os.path.splitext(it["path"])[1].lower().lstrip(".") or "jpg",
        "kind": "image",
        "size": it["size"],
        "mtime": it["mtime"],
        "ts": ts,
        "time": time.strftime("%m-%d %H:%M", time.localtime(ts)),
        "ageSec": int(age),
        "fresh": age < 600,
        "session": it.get("session"),
        "sessionName": session_label(it.get("session") or ""),
        "source": it.get("source"),
        "w": w, "h": h,
        "a4dpi": dpi,
        "suggestPaper": sug,
        "suggestDpi": sug_dpi,
        "quality": "清晰" if dpi >= 200 else ("一般" if dpi >= 120 else "偏软"),
    }


# 常见纸张的物理尺寸（毫米），用来给用户推荐「这张图打多大的纸才够清晰」
_PAPER_MM = {
    "4R": (102.0, 152.0), "A6": (105.0, 148.0), "5R": (127.0, 178.0),
    "A5": (148.0, 210.0), "B5": (176.0, 250.0), "A4": (210.0, 297.0),
}


def _fit_dpi(w: float, h: float, pw: float, ph: float) -> float:
    """图片等比「适应」到纸上时的实际打印 dpi。

    纸有横竖两种摆法，打印软件会选能把图铺得最大的那种——铺得越大 dpi 越低，
    所以要取两种摆法里 dpi 较小的（=图较大的）那个，才是真实出来的效果。
    """
    port = max(w / (pw / 25.4), h / (ph / 25.4))     # 图不转
    land = max(w / (ph / 25.4), h / (pw / 25.4))     # 图转 90°
    return min(port, land)


def suggest_paper(w: int, h: int):
    """A4 够看得就直接打 A4；实在太低清，就挑一张 dpi 最高的小纸。

    720x405 的图打 A4 只有 62dpi（一放大就发虚），改打 A6 能到 124dpi——
    同样是聊天截图，换个尺寸差别很明显，所以值得推荐。
    """
    a4 = int(round(_fit_dpi(w, h, 210.0, 297.0)))
    if a4 >= 120:
        return "A4", a4
    best = ("A4", a4)
    for name in ("B5", "A5", "5R", "A6", "4R"):
        pw, ph = _PAPER_MM[name]
        d = int(round(_fit_dpi(w, h, pw, ph)))
        if d > best[1]:
            best = (name, d)
    return best


def image_sessions(hours=720, min_edge=_MIN_EDGE):
    """列出有哪些会话有图片，供前端做来源筛选。"""
    items = scan_images(hours=hours, limit=_MAX_IMAGES, min_edge=min_edge)
    agg = {}
    for it in items:
        h = it.get("session")
        if not h:
            continue
        if h not in agg:
            agg[h] = {"id": h, "name": session_label(h), "count": 0,
                      "latest": it["ts"]}
        agg[h]["count"] += 1
        agg[h]["latest"] = max(agg[h]["latest"], it["ts"])
    out = sorted(agg.values(), key=lambda x: x["latest"], reverse=True)
    # 文件传输助手排最前——大部分人就是用它传东西的
    out.sort(key=lambda x: 0 if x["id"] == FILEHELPER_ID else 1)
    return out
