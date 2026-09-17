# -*- coding: utf-8 -*-
"""多图版面合成引擎。

把「一组条目 + 位置 + 裁切 + 旋转 + 标题」渲染成一页页精确等于打印画布的位图。
renderer.py 里那套单页逻辑已经跑通了，这里不碰它，独立成模块。

条目的坐标全部用「页面归一化比例」描述（0~1），前端编辑器和服务端共用同一套数学，
保证手机上看到的排版和打出来的完全一致。
"""
from __future__ import annotations

from typing import Callable, List, Optional

from PIL import Image, ImageDraw

from renderer import MM_PER_INCH, load_font, to_gray

MAX_CROP = 0.45          # 单边最多裁掉 45%，再多就是误操作了
MIN_SOURCE_DPI = 72
MAX_SOURCE_DPI = 600
MAX_SOURCE_PX = 4200     # 源图单边像素上限，防止长图把内存撑爆
CAPTION_MAX_FRAC = 0.26  # 标题最多吃掉条目高度的 26%


def _flt(v, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    return f


def crop_pixels(size, crop: dict):
    """把四边裁切比例换算成源图像素坐标 (left, top, right, bottom)。"""
    w, h = size
    l = min(MAX_CROP, _flt((crop or {}).get("l")))
    r = min(MAX_CROP, _flt((crop or {}).get("r")))
    t = min(MAX_CROP, _flt((crop or {}).get("t")))
    b = min(MAX_CROP, _flt((crop or {}).get("b")))
    left = int(round(l * w))
    top = int(round(t * h))
    right = w - int(round(r * w))
    bottom = h - int(round(b * h))
    if right - left < 2:
        left, right = 0, w
    if bottom - top < 2:
        top, bottom = 0, h
    return (max(0, left), max(0, top), min(w, right), min(h, bottom))


def _contain(img: Image.Image, bw: int, bh: int) -> Optional[Image.Image]:
    """等比缩小到框内（contain）。"""
    iw, ih = img.size
    if iw < 1 or ih < 1 or bw < 2 or bh < 2:
        return None
    s = min(bw / float(iw), bh / float(ih))
    nw, nh = max(1, int(round(iw * s))), max(1, int(round(ih * s)))
    if (nw, nh) == (iw, ih):
        return img
    return img.resize((nw, nh), Image.LANCZOS)


def _trim_to_width(draw, text, font, max_w):
    """标题太宽就截断加省略号，不放任它溢出到相邻条目上。"""
    if not text or max_w <= 4:
        return ""
    try:
        if draw.textlength(text, font=font) <= max_w:
            return text
    except Exception:
        if len(text) * font.size * 0.62 <= max_w:
            return text
    lo, hi, best = 0, len(text), ""
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = text[:mid] + "…"
        try:
            w = draw.textlength(cand, font=font)
        except Exception:
            w = len(cand) * font.size * 0.62
        if w <= max_w:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _draw_caption(canvas, box, caption, canvas_px):
    """把标题画在条目矩形底部预留的横条里，居中。"""
    x, y, w, h = box
    band = min(int(h * CAPTION_MAX_FRAC), int(canvas_px[1] * 0.042))
    band = max(band, int(canvas_px[1] * 0.018))
    if band < 8 or w < 8:
        return 0
    image_h = max(1, h - band)
    d = ImageDraw.Draw(canvas)
    fs = max(8, int(band * 0.62))
    font = load_font(fs)
    shown = _trim_to_width(d, caption, font, w - 6)
    if not shown:
        return band
    try:
        tw = d.textlength(shown, font=font)
    except Exception:
        tw = len(shown) * fs * 0.62
    d.text((x + (w - tw) / 2.0, y + image_h + (band - fs) / 2.0), shown,
           font=font, fill=(40, 40, 40))
    return band


def _source_dpi(nat_mm, crop, box, canvas_px) -> float:
    """估算源图该按多少 dpi 渲染，保证贴到画布上时不糊也不浪费内存。"""
    cw_frac = max(0.02, 1 - min(MAX_CROP, _flt(crop.get("l"))) - min(MAX_CROP, _flt(crop.get("r"))))
    ch_frac = max(0.02, 1 - min(MAX_CROP, _flt(crop.get("t"))) - min(MAX_CROP, _flt(crop.get("b"))))
    nat_w_mm = max(1e-3, float(nat_mm[0]))
    nat_h_mm = max(1e-3, float(nat_mm[1]))
    eff_a = (nat_w_mm * cw_frac) / (nat_h_mm * ch_frac)
    bw, bh = box[2], box[3]

    need_w = min(max(bw, bh * eff_a) * 1.25, MAX_SOURCE_PX)
    need_h = min(max(bh, bw / eff_a) * 1.25, MAX_SOURCE_PX) if eff_a > 0 else need_w
    dpi_w = need_w / max(1e-3, (nat_w_mm * cw_frac) / MM_PER_INCH)
    dpi_h = need_h / max(1e-3, (nat_h_mm * ch_frac) / MM_PER_INCH)
    return max(MIN_SOURCE_DPI, min(MAX_SOURCE_DPI, max(dpi_w, dpi_h)))


def compose_pages(resolver: Callable[[str], object], pages_spec: list,
                  canvas_px, gray: bool = False, log_fn=None) -> List[Image.Image]:
    """渲染整份版面。

    resolver(jid) -> Source；pages_spec 是 [{"items":[...]}, ...]。
    每条 item: {jid, idx, x, y, w, h, rot, crop:{t,r,b,l}, caption}
    坐标为页面归一化比例。
    """
    cw_px, ch_px = canvas_px
    out: List[Image.Image] = []
    cache = {}

    for pno, spec in enumerate(pages_spec or []):
        canvas = Image.new("RGB", (cw_px, ch_px), "white")
        for item in (spec.get("items") or []):
            jid = item.get("jid")
            if jid not in cache:
                try:
                    cache[jid] = resolver(jid)
                except Exception as e:
                    if log_fn:
                        log_fn("第%d页：取不到素材 %s：%s" % (pno + 1, jid, e))
                    cache[jid] = None
            src = cache.get(jid)
            if src is None:
                continue
            try:
                n = getattr(src, "page_count", 1) or 1
                idx = max(0, min(int(_flt(item.get("idx"), 0)), n - 1))
                w = max(0.01, _flt(item.get("w"), 0.1))
                h = max(0.01, _flt(item.get("h"), 0.1))
                box = (int(round(_flt(item.get("x")) * cw_px)),
                       int(round(_flt(item.get("y")) * ch_px)),
                       max(2, int(round(w * cw_px))),
                       max(2, int(round(h * ch_px))))
                crop = item.get("crop") or {}

                nat_mm = src.page_size_mm(idx)
                dpi = _source_dpi(nat_mm, crop, box, canvas_px)
                try:
                    img = src.render(idx, dpi)
                except TypeError:
                    img = src.render(idx)
                img = img.convert("RGB")
                img = img.crop(crop_pixels(img.size, crop))

                rot = _flt(item.get("rot"))
                if abs(rot) > 0.01:
                    # PIL 逆时针为正，这里统一成顺时针为正，跟 CSS rotate 对齐
                    img = img.rotate(-rot, expand=True, fillcolor="white",
                                     resample=Image.BICUBIC)

                cap = (item.get("caption") or "").strip()
                band = _draw_caption(canvas, box, cap, canvas_px) if cap else 0
                fitted = _contain(img, box[2], max(1, box[3] - band))
                if fitted is not None:
                    px = box[0] + (box[2] - fitted.width) // 2
                    py = box[1] + (max(1, box[3] - band) - fitted.height) // 2
                    canvas.paste(fitted, (px, py))
                del img, fitted
            except Exception as e:
                if log_fn:
                    log_fn("第%d页渲染条目失败：%s" % (pno + 1, e))
        out.append(to_gray(canvas) if gray else canvas)

    for s in cache.values():
        try:
            if s is not None:
                s.close()
        except Exception:
            pass
    return out
