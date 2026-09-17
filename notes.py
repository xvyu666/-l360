# -*- coding: utf-8 -*-
"""笔记：手机上写的字，能存下来、能分类、下次打开还在。

一份笔记一个 json 文件放在 runtime/notes/，正文是 HTML（富文本：标题、加粗、列表）。
纯文本也保留一份（notes.text），用来做列表摘要和搜索，打印时不用它。
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.path.join(HERE, "runtime")
NOTES = os.path.join(RUNTIME, "notes")
DEFAULT_CAT = "未分类"
MAX_KEEP = 500


def ensure():
    os.makedirs(NOTES, exist_ok=True)


def _path(nid: str) -> str:
    return os.path.join(NOTES, "%s.json" % nid)


def _plain(html: str) -> str:
    """从 HTML 里剥出纯文本，给列表摘要用。"""
    s = re.sub(r"<br\s*/?>", "\n", html or "", flags=re.I)
    s = re.sub(r"</(p|div|h[1-6]|li|blockquote)>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _title_of(note: dict) -> str:
    t = (note.get("title") or "").strip()
    if t:
        return t
    plain = note.get("text") or _plain(note.get("html", ""))
    first = plain.strip().split("\n", 1)[0]
    return (first[:24] + "…") if len(first) > 24 else (first or "无标题")


def load(nid: str) -> dict | None:
    p = _path(nid)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def items(cat: str = "", limit: int = 300) -> list:
    ensure()
    out = []
    for f in os.listdir(NOTES):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(NOTES, f), "r", encoding="utf-8") as fh:
                n = json.load(fh)
        except Exception:
            continue
        if cat and (n.get("cat") or DEFAULT_CAT) != cat:
            continue
        out.append(n)
    out.sort(key=lambda n: n.get("updated", 0), reverse=True)
    return out[:limit]


def cats() -> list:
    """分类清单 + 每个分类有多少篇，前端拿去做侧边栏。"""
    counter = {}
    for n in items(limit=5000):
        c = n.get("cat") or DEFAULT_CAT
        counter[c] = counter.get(c, 0) + 1
    out = [{"name": c, "count": n} for c, n in counter.items()]
    out.sort(key=lambda x: (-x["count"], x["name"]))
    return out


def public(note: dict) -> dict:
    return {
        "id": note.get("id", ""),
        "title": _title_of(note),
        "cat": note.get("cat") or DEFAULT_CAT,
        "updated": note.get("updated", 0),
        "created": note.get("created", 0),
        "chars": len(note.get("text") or ""),
        "brief": (note.get("text") or "")[:60].replace("\n", " "),
    }


def save(data: dict) -> dict:
    """新建或更新。有 id 就更新，没有就新建。"""
    ensure()
    nid = (data.get("id") or "").strip()
    now = time.time()
    old = load(nid) if nid else None

    html = data.get("html") or ""
    text = _plain(html)
    note = {
        "id": nid or uuid.uuid4().hex[:12],
        "title": (data.get("title") or "").strip()[:80],
        "html": html,
        "text": text,
        "cat": (data.get("cat") or (old or {}).get("cat") or DEFAULT_CAT).strip()[:40] or DEFAULT_CAT,
        "pt": int(data.get("pt") or (old or {}).get("pt") or 12),
        "created": (old or {}).get("created", now),
        "updated": now,
    }
    with open(_path(note["id"]), "w", encoding="utf-8") as f:
        json.dump(note, f, ensure_ascii=False, indent=1)
    prune()
    return note


def remove(nid: str) -> bool:
    p = _path(nid)
    if os.path.isfile(p):
        try:
            os.remove(p)
            return True
        except Exception:
            return False
    return False


def prune(keep: int = MAX_KEEP):
    try:
        all_notes = items(limit=10000)
        for n in all_notes[keep:]:
            try:
                os.remove(_path(n["id"]))
            except Exception:
                pass
    except Exception:
        pass


def count() -> int:
    ensure()
    return len([f for f in os.listdir(NOTES) if f.endswith(".json")])
