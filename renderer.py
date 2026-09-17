# -*- coding: utf-8 -*-
"""把各种来源的文件统一渲染成一页页图像，供打印机照图打印。

支持：图片/截图（含 HEIC）、PDF、Office 三件套（后台转 PDF）、纯文本。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

try:  # HEIC/HEIF（安卓/iPhone 相册常见格式）
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_OK = True
except Exception:
    HEIF_OK = False

MM_PER_INCH = 25.4
DEFAULT_SOURCE_DPI = 96.0

IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "bmp", "gif", "tif", "tiff", "heic", "heif", "ico", "jfif", "avif"}
PDF_EXT = {"pdf"}
OFFICE_EXT = {"doc", "docx", "rtf", "odt", "dot", "dotx", "docm", "mht", "mhtml", "htm", "html",
              "xls", "xlsx", "xlsm", "xlsb", "csv", "ods",
              "ppt", "pptx", "pptm", "pps", "ppsx", "odp"}
TEXT_EXT = {"txt", "md", "log", "json", "xml", "ini", "cfg", "sql", "py", "js", "css", "html", "htm", "yml", "yaml"}

CHINESE_FONT = None


def _font_path():
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/msyh.ttf"):
        if os.path.exists(p):
            return p
    return None


def load_font(size_px: int):
    global CHINESE_FONT
    if CHINESE_FONT is None:
        CHINESE_FONT = _font_path()
    if CHINESE_FONT:
        try:
            return ImageFont.truetype(CHINESE_FONT, size_px)
        except Exception:
            pass
    return ImageFont.load_default()


# ---------------------------------------------------------------- 基础工具


def mm_to_px(mm: float, dpi: float) -> int:
    return max(1, int(round(mm / MM_PER_INCH * dpi)))


def px_to_mm(px: int, dpi: float) -> float:
    return px / dpi * MM_PER_INCH


def mm_size(width_px: int, height_px: int, dpi: float) -> Tuple[float, float]:
    return (px_to_mm(width_px, dpi), px_to_mm(height_px, dpi))


def wrap_tokens(text: str) -> List[str]:
    """把文本切成可换行的片段：中文逐字，英文/数字按词。"""
    tokens, buf = [], ""
    for ch in text:
        if ch in " \t":
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        elif _is_cjk(ch):
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    return tokens


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x1100 <= code <= 0x11FF or 0x2E80 <= code <= 0x303F or
        0x3040 <= code <= 0x30FF or 0x3130 <= code <= 0x318F or
        0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or
        0xA000 <= code <= 0xA4CF or 0xAC00 <= code <= 0xD7AF or
        0xF900 <= code <= 0xFAFF or 0xFE30 <= code <= 0xFE4F or
        0xFF00 <= code <= 0xFF60 or 0xFFE0 <= code <= 0xFFE6 or
        0x20000 <= code <= 0x2FA1F
    )


def to_gray(img: Image.Image) -> Image.Image:
    return ImageOps.grayscale(img).convert("RGB")


# ---------------------------------------------------------------- 来源抽象


class Source:
    """一个可打印来源：知道有几页、每页物理尺寸、以及怎么把某一页画出来。"""

    kind = "unknown"

    def __init__(self):
        self.page_count = 1

    def page_size_mm(self, index: int) -> Tuple[float, float]:
        raise NotImplementedError

    def render(self, index: int, dpi: float) -> Image.Image:
        raise NotImplementedError

    def close(self):
        pass


class ImageSource(Source):
    kind = "image"

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self._img = Image.open(path)
        try:
            self._img = ImageOps.exif_transpose(self._img)
        except Exception:
            pass
        if self._img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", self._img.size, "white")
            self._img.convert("RGBA")
            bg.paste(self._img.convert("RGBA"), mask=self._img.convert("RGBA").split()[-1])
            self._img = bg
        elif self._img.mode not in ("RGB", "L"):
            self._img = self._img.convert("RGB")
        else:
            self._img = self._img.convert(self._img.mode)
        self.page_count = 1
        dpi_meta = self._img.info.get("dpi")
        try:
            dpi_x = float(dpi_meta[0]) if dpi_meta and dpi_meta[0] else DEFAULT_SOURCE_DPI
        except Exception:
            dpi_x = DEFAULT_SOURCE_DPI
        if dpi_x <= 0 or dpi_x > 1200:
            dpi_x = DEFAULT_SOURCE_DPI
        self._dpi = dpi_x

    def page_size_mm(self, index: int = 0):
        return mm_size(self._img.width, self._img.height, self._dpi)

    def render(self, index: int = 0, dpi: float = 200):
        return self._img.convert("RGB")


class PdfSource(Source):
    kind = "pdf"

    def __init__(self, path: str):
        super().__init__()
        import pypdfium2 as pdfium

        self.path = path
        self._pdfium = pdfium
        self.doc = pdfium.PdfDocument(path)
        self.page_count = len(self.doc)
        self._sizes = []
        for i in range(self.page_count):
            try:
                w, h = self.doc[i].get_size()
            except Exception:
                w, h = 595.0, 842.0
            self._sizes.append((float(w), float(h)))

    def page_size_mm(self, index: int = 0):
        w, h = self._sizes[index]
        return (w / 72.0 * MM_PER_INCH, h / 72.0 * MM_PER_INCH)

    def render(self, index: int = 0, dpi: float = 200):
        page = self.doc[index]
        scale = dpi / 72.0
        res = page.render(scale=scale)
        if hasattr(res, "to_pil"):
            img = res.to_pil()
        elif hasattr(res, "get_pil"):
            img = res.get_pil()
        else:
            raise RuntimeError("不支持的 pdfium 渲染返回类型")
        img = img.convert("RGB")
        return img

    def close(self):
        try:
            self.doc.close()
        except Exception:
            pass


class TextSource(Source):
    """手写文本：按 A4 竖版排版，打印时会再按比例适配到实际纸张。"""

    kind = "text"

    def __init__(self, text: str, font_pt: int = 12):
        super().__init__()
        self.text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        self.font_pt = int(font_pt) or 12
        self.paper_mm = (210.0, 297.0)
        self.margin_mm = 18.0
        self._layout_cache = {}

    def _layout(self, dpi: float):
        key = int(dpi)
        if key in self._layout_cache:
            return self._layout_cache[key]
        pw = mm_to_px(self.paper_mm[0], dpi)
        ph = mm_to_px(self.paper_mm[1], dpi)
        margin = mm_to_px(self.margin_mm, dpi)
        font = load_font(mm_to_px(self.font_pt / 72.0 * MM_PER_INCH, dpi))
        line_gap = int(mm_to_px(self.font_pt / 72.0 * MM_PER_INCH, dpi) * 0.75)
        max_width = pw - margin * 2
        max_height = ph - margin * 2

        lines = []
        for paragraph in self.text.split("\n"):
            if paragraph == "":
                lines.append("")
                continue
            cur = ""
            cur_w = 0
            for tok in wrap_tokens(paragraph):
                w = font.getlength(tok)
                if cur_w + w > max_width and cur:
                    lines.append(cur)
                    cur = "" if tok in " \t" else tok
                    cur_w = font.getlength(cur)
                else:
                    cur += tok
                    cur_w += w
            if cur:
                lines.append(cur)

        line_hfw = font.getbbox("中")[3] if hasattr(font, "getbbox") else 0
        line_h = line_hfw + line_gap if line_hfw else line_gap * 2
        per_page = max(1, max_height // line_h)
        pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]
        result = (pw, ph, margin, font, line_h, pages)
        self._layout_cache[key] = result
        return result

    def page_count_prop(self):
        pw, ph, margin, font, line_h, pages = self._layout(96)
        return max(1, len(pages))

    def page_size_mm(self, index: int = 0):
        return self.paper_mm

    def render(self, index: int = 0, dpi: float = 200):
        pw, ph, margin, font, line_h, pages = self._layout(dpi)
        img = Image.new("RGB", (pw, ph), "white")
        draw = ImageDraw.Draw(img)
        y = margin
        for line in pages[index] if index < len(pages) else []:
            draw.text((margin, y), line, font=font, fill=(0, 0, 0))
            y += line_h
        return img

    def _ensure_pages(self):
        self.page_count = self.page_count_prop()


def make_text_source(text: str, font_pt: int = 12) -> TextSource:
    src = TextSource(text, font_pt)
    src._ensure_pages()
    return src


# ---------------------------------------------------------------- Office 转换


# Office→PDF 很慢（约 6 秒一次），而且多个 Word 实例并发容易互相打架。
# 这里按源文件做缓存：同一个文档只转换一次，之后预览/打印/翻页都直接复用。
_PDF_CACHE: dict = {}
_PDF_LOCKS: dict = {}
_PDF_REGISTRY_LOCK = threading.Lock()


def _file_locks(path: str) -> threading.Lock:
    with _PDF_REGISTRY_LOCK:
        lock = _PDF_LOCKS.get(path)
        if lock is None:
            lock = threading.Lock()
            _PDF_LOCKS[path] = lock
        return lock


def _source_stamp(path: str):
    try:
        st = os.stat(path)
        return (st.st_size, int(st.st_mtime))
    except OSError:
        return None


def office_to_pdf(src: str, timeout: int = 120) -> str:
    """把 Office 文档转成 PDF，结果按文件缓存并串行转换。

    以前是每次调用都先删掉旧 PDF 再重转，导致并发的预览请求互相把对方的
    文件删掉，表现就是「预览打不开 / 图片空白」。现在改成：命中缓存直接复用，
    未命中时先转到一个临时文件，成功后再原子替换，读到的永远是完整文件。
    """
    src_abs = os.path.abspath(src)
    dst = os.path.join(os.path.dirname(src_abs), "_converted.pdf")
    stamp = _source_stamp(src_abs)

    with _PDF_REGISTRY_LOCK:
        hit = _PDF_CACHE.get(src_abs)
    if hit and hit[0] == stamp and os.path.exists(hit[1]):
        return hit[1]

    lock = _file_locks(src_abs)
    with lock:
        # 排队期间可能已被别人转好
        with _PDF_REGISTRY_LOCK:
            hit = _PDF_CACHE.get(src_abs)
        if hit and hit[0] == stamp and os.path.exists(hit[1]):
            return hit[1]

        tmp = os.path.join(os.path.dirname(src_abs), "_%s.pdf" % uuid.uuid4().hex[:8])
        cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "office.py"),
               src_abs, tmp]
        proc = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0 or not os.path.exists(tmp):
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            msg = (proc.stderr or "").strip()[-300:] or (proc.stdout or "").strip()[-300:]
            raise RuntimeError(msg or "Office 转换失败")
        # 原子替换：避免别的请求读到写了一半的 PDF
        os.replace(tmp, dst)
        with _PDF_REGISTRY_LOCK:
            _PDF_CACHE[src_abs] = (stamp, dst)
        return dst


def office_available() -> bool:
    return sys.platform.startswith("win")


# ---------------------------------------------------------------- 统一入口


def classify(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in IMAGE_EXT:
        return "image"
    if ext in PDF_EXT:
        return "pdf"
    if ext in OFFICE_EXT:
        return "office"
    if ext in TEXT_EXT:
        return "text"
    return "unknown"


def open_source(path: str) -> Source:
    kind = classify(path)
    if kind == "image":
        if not HEIF_OK and os.path.splitext(path)[1].lower().lstrip(".") in ("heic", "heif", "avif"):
            raise RuntimeError("暂不支持 HEIC 格式，请在手机上改选 JPG 再上传")
        return ImageSource(path)
    if kind == "pdf":
        return PdfSource(path)
    if kind == "office":
        pdf = office_to_pdf(path)
        return PdfSource(pdf)
    if kind == "text":
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception:
            text = ""
        return make_text_source(text)
    raise RuntimeError("不支持的文件类型：%s" % (os.path.splitext(path)[1] or "未知"))


# ---------------------------------------------------------------- 排版


def compose_page(src_img: Image.Image, src_mm: Tuple[float, float],
                 canvas_size: Tuple[int, int], canvas_mm: Tuple[float, float],
                 mode: str = "fit", grayscale: bool = False) -> Image.Image:
    """把一页源图摆到目标纸张画布上。

    mode: fit=完整放进纸张并居中（留白）；fill=铺满纸张（会裁掉边缘）；natural=按原始物理尺寸打印。
    """
    cw, ch = canvas_size
    iw, ih = src_img.size
    canvas_dpi = cw / (canvas_mm[0] / MM_PER_INCH)

    if mode == "natural":
        tw = mm_to_px(src_mm[0], canvas_dpi)
        th = mm_to_px(src_mm[1], canvas_dpi)
    else:
        ratio_w = cw / float(iw)
        ratio_h = ch / float(ih)
        ratio = min(ratio_w, ratio_h) if mode != "fill" else max(ratio_w, ratio_h)
        tw, th = max(1, int(iw * ratio)), max(1, int(ih * ratio))

    if grayscale:
        src_img = to_gray(src_img)

    if mode == "fill":
        tmp = src_img.resize((tw, th), Image.LANCZOS)
        left = max(0, (tw - cw) // 2)
        top = max(0, (th - ch) // 2)
        return tmp.crop((left, top, left + cw, top + ch)).convert("RGB")

    if (tw, th) != src_img.size:
        tmp = src_img.resize((tw, th), Image.LANCZOS)
    else:
        tmp = src_img
    canvas = Image.new("RGB", (cw, ch), "white")
    left = max(0, (cw - tw) // 2)
    top = max(0, (ch - th) // 2)
    if mode == "natural" and (tw > cw or th > ch):
        part = tmp.crop((max(0, (tw - cw) // 2), max(0, (th - ch) // 2),
                         max(0, (tw - cw) // 2) + min(tw, cw), max(0, (th - ch) // 2) + min(th, ch)))
        canvas.paste(part, (max(0, (cw - min(tw, cw)) // 2), max(0, (ch - min(th, ch)) // 2)))
    else:
        canvas.paste(tmp.convert("RGB"), (left, top))
    return canvas
