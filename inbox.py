# -*- coding: utf-8 -*-
"""手机中转收件箱。

安卓中转 APP 注册成系统分享接收者之后，微信 / 浏览器 / 相册 / WPS 里点「分享」
就能把文件送过来。APP 会把文件存进手机固定文件夹，同时（如果填了电脑地址）
自动 POST 到这里，落进 runtime/inbox/。

网页端这边列出来的就是手机刚分享过来的东西，勾选即打印。
网页本身没法扫手机文件夹（浏览器的沙箱限制），所以「自动上传」那条路是真正打通的关键。
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.path.join(HERE, "runtime")
INBOX = os.path.join(RUNTIME, "inbox")

MAX_KEEP = 200          # 收件箱最多留多少个，超了删最旧的
MAX_SIZE = 200 * 1024 * 1024   # 单个文件上限，和上传一致


def ensure():
    os.makedirs(INBOX, exist_ok=True)


def _safe(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/")) or "unnamed"
    return re.sub(r'[\\/:*?"<>|]+', "_", name)[:120]


def _meta_path(path: str) -> str:
    return path + ".meta.json"


def add(data: bytes, name: str, origin: str = "phone") -> dict:
    """存一个文件进收件箱，返回它的描述。"""
    ensure()
    if not data:
        raise ValueError("内容是空的")
    if len(data) > MAX_SIZE:
        raise ValueError("文件太大（上限 %d MB）" % (MAX_SIZE // 1048576))

    sid = uuid.uuid4().hex[:12]
    safe = _safe(name)
    stem, ext = os.path.splitext(safe)
    ts = time.time()
    stamp = time.strftime("%m%d-%H%M%S", time.localtime(ts))
    fname = "%s_%s_%s%s" % (stamp, sid[:6], stem[:60], ext)
    path = os.path.join(INBOX, fname)

    with open(path, "wb") as f:
        f.write(data)
    meta = {
        "id": sid, "name": safe, "path": path, "size": len(data),
        "ts": ts, "ext": ext.lower().lstrip("."), "origin": origin,
    }
    try:
        with open(_meta_path(path), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    except Exception:
        pass
    prune()
    return meta


def _read_meta(path: str) -> dict | None:
    mp = _meta_path(path)
    if os.path.isfile(mp):
        try:
            with open(mp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def items(limit: int = 200) -> list:
    """列出收件箱内容，新的在前。"""
    ensure()
    out = []
    for f in os.listdir(INBOX):
        if f.endswith(".meta.json"):
            continue
        path = os.path.join(INBOX, f)
        if not os.path.isfile(path):
            continue
        meta = _read_meta(path)
        if meta is None:
            # 手动拷进来的文件没有 meta，按文件名和 mtime 兜底显示
            meta = {
                "id": uuid.uuid4().hex[:12], "name": f, "path": path,
                "size": os.path.getsize(path), "ts": os.path.getmtime(path),
                "ext": os.path.splitext(f)[1].lower().lstrip("."), "origin": "manual",
            }
        if not os.path.isfile(meta.get("path", "")):
            meta["path"] = path
        out.append(meta)
    out.sort(key=lambda m: m.get("ts", 0), reverse=True)
    return out[:limit]


def public(meta: dict) -> dict:
    return {
        "id": meta.get("id", ""), "name": meta.get("name", ""),
        "size": meta.get("size", 0), "ts": meta.get("ts", 0),
        "ext": meta.get("ext", ""), "origin": meta.get("origin", "phone"),
    }


def resolve(sid: str) -> str | None:
    for m in items(limit=MAX_KEEP * 2):
        if m.get("id") == sid and os.path.isfile(m.get("path", "")):
            return m["path"]
    return None


def resolve_many(sids: list) -> list:
    got = []
    for sid in sids:
        p = resolve(sid)
        if p:
            got.append((sid, p))
    return got


def remove(sid: str) -> bool:
    for m in items(limit=MAX_KEEP * 2):
        if m.get("id") == sid:
            p = m.get("path", "")
            for f in (p, _meta_path(p)):
                try:
                    if f and os.path.isfile(f):
                        os.remove(f)
                except Exception:
                    pass
            return True
    return False


def clear():
    ensure()
    n = 0
    for f in os.listdir(INBOX):
        try:
            os.remove(os.path.join(INBOX, f))
            n += 1
        except Exception:
            pass
    return n


def prune(keep: int = MAX_KEEP):
    """收件箱不是仓库，超量就丢最旧的，免得占着磁盘。"""
    try:
        all_items = items(limit=10000)
        for m in all_items[keep:]:
            p = m.get("path", "")
            for f in (p, _meta_path(p)):
                try:
                    if f and os.path.isfile(f):
                        os.remove(f)
                except Exception:
                    pass
    except Exception:
        pass


def count() -> int:
    ensure()
    return len([f for f in os.listdir(INBOX) if not f.endswith(".meta.json")])
