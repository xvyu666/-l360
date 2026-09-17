# -*- coding: utf-8 -*-
"""把笔记的 HTML 渲染成可打印的页面。

手机上用 contenteditable 写出来的东西是一小段 HTML（<h1>/<p>/<b>/<li>…）。
要打印就得自己排版——没有浏览器可用，也不想为了这个拖一个 wkhtmltopdf 进来。

所以这里用 PIL 直接画：解析成「块」→ 每块按字号/粗斜体切成行 → 逐行画出来。
支持：标题三级、段落、换行、粗体、斜体、下划线、有序/无序列表、引用、分割线、字号。

已知简化：斜体是用错切变换模拟的（微软雅黑没有真斜体字形），
列表/缩进按字号倍数算，不是 CSS 盒模型——笔记够用，别拿它当浏览器。
"""
from __future__ import annotations

import os
import re

from html.parser import HTMLParser
from PIL import Image, ImageDraw

import renderer as RD

# <font size="N"> 的相对倍数（3 号 = 正文字号）
FONT_SIZE_SCALE = {1: 0.72, 2: 0.86, 3: 1.0, 4: 1.25, 5: 1.6, 6: 2.1, 7: 2.9}
HEADING_SCALE = {"h1": 1.75, "h2": 1.4, "h3": 1.18}
BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "blockquote", "li", "pre"}
LIST_TAGS = {"ul", "ol"}


class _Parser(HTMLParser):
    """把 contenteditable 产出的 HTML 解析成块列表。"""

    def __init__(self, base_pt: int = 12):
        super().__init__(convert_charrefs=True)
        self.base = base_pt
        self.blocks = []
        self._runs = []
        self._bold = 0
        self._italic = 0
        self._under = 0
        self._sizes = []
        self._type = "p"
        self._lists = []      # [('ul'|'ol', 计数器列表)]
        self._quote = 0

    # -- 内部小工具
    def _pt(self):
        scale = self._sizes[-1] if self._sizes else 1.0
        return max(5.0, self.base * scale)

    def _flush(self):
        runs = [r for r in self._runs if r[0]]
        if runs:
            self.blocks.append({
                "type": self._type,
                "runs": runs,
                "ordered": bool(self._lists) and self._lists[-1][0] == "ol",
                "index": self._lists[-1][1] if self._lists else 0,
                "quote": self._quote > 0,
            })
        self._runs = []

    def _emit(self, text):
        if not text:
            return
        self._runs.append((text, self._bold > 0, self._italic > 0, self._under > 0,
                           self._pt(), self._quote > 0))

    # -- HTMLParser 回调
    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("h1", "h2", "h3"):
            self._flush()
            self._type = tag
            self._sizes.append(HEADING_SCALE[tag])
            self._bold += 1
            return
        if tag in ("p", "div", "pre"):
            self._flush()
            self._type = "pre" if tag == "pre" else "p"
            return
        if tag == "blockquote":
            self._flush()
            self._type = "p"
            self._quote += 1
            return
        if tag in LIST_TAGS:
            self._flush()
            self._lists.append((tag, 0))
            return
        if tag == "li":
            self._flush()
            self._type = "li"
            if self._lists:
                kind, n = self._lists[-1]
                self._lists[-1] = (kind, n + 1)
            return
        if tag in ("b", "strong"):
            self._bold += 1
            return
        if tag in ("i", "em"):
            self._italic += 1
            return
        if tag == "u":
            self._under += 1
            return
        if tag == "br":
            self._emit("\n")
            return
        if tag == "hr":
            self._flush()
            self.blocks.append({"type": "hr", "runs": [], "index": 0, "quote": False})
            return
        if tag == "font" and a.get("size"):
            try:
                self._sizes.append(FONT_SIZE_SCALE.get(int(a["size"]), 1.0))
            except ValueError:
                pass
            return

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in ("br", "hr"):
            return
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3"):
            self._flush()
            self._bold = max(0, self._bold - 1)
            if self._sizes:
                self._sizes.pop()
            self._type = "p"
        elif tag in ("p", "div", "pre", "li"):
            self._flush()
            self._type = "p"
        elif tag == "blockquote":
            self._flush()
            self._quote = max(0, self._quote - 1)
        elif tag in LIST_TAGS:
            self._flush()
            if self._lists:
                self._lists.pop()
        elif tag in ("b", "strong"):
            self._bold = max(0, self._bold - 1)
        elif tag in ("i", "em"):
            self._italic = max(0, self._italic - 1)
        elif tag == "u":
            self._under = max(0, self._under - 1)
        elif tag == "font":
            if self._sizes:
                self._sizes.pop()

    def handle_data(self, data):
        if data:
            self._emit(data)

    def result(self):
        self._flush()
        return self.blocks


def parse(html: str, base_pt: int = 12) -> list:
    p = _Parser(base_pt)
    try:
        p.feed(html or "")
        p.close()
    except Exception:
        pass
    return p.result()


class NoteSource(RD.Source):
    """一份笔记 = 若干页 A4 竖版位图。"""

    kind = "note"

    def __init__(self, blocks: list, base_pt: int = 12,
                 paper_mm=(210.0, 297.0), margin_mm=18.0):
        super().__init__()
        self.blocks = blocks or []
        self.base_pt = int(base_pt) or 12
        self.paper_mm = tuple(paper_mm)
        self.margin_mm = margin_mm
        self._cache = {}

    # -- 排版
    def _layout(self, dpi: float):
        key = int(dpi)
        if key in self._cache:
            return self._cache[key]

        pw = RD.mm_to_px(self.paper_mm[0], dpi)
        ph = RD.mm_to_px(self.paper_mm[1], dpi)
        margin = RD.mm_to_px(self.margin_mm, dpi)
        max_w = pw - margin * 2
        max_h = ph - margin * 2
        base_px = RD.mm_to_px(self.base_pt / 72.0 * RD.MM_PER_INCH, dpi)

        fonts = {}

        def font_of(pt):
            px = max(6, int(round(base_px * pt / float(self.base_pt))))
            if px not in fonts:
                fonts[px] = RD.load_font(px)
            return fonts[px]

        lines = []   # 每行: (x偏移, 基线高度, [(token, bold, italic, under, pt, quote)])
        for blk in self.blocks:
            btype = blk.get("type")
            if btype == "hr":
                lines.append(("hr", base_px, []))
                lines.append(("gap", int(base_px * 0.5), []))
                continue

            # 1) 把 runs 摊成 token
            tokens = []
            for text, bold, italic, under, pt, quote in blk.get("runs", []):
                for piece in text.split("\n"):
                    for tok in RD.wrap_tokens(piece):
                        tokens.append((tok, bold, italic, under, pt, quote))
                    tokens.append(("\n", bold, italic, under, pt, quote))
                if tokens and tokens[-1][0] == "\n":
                    tokens.pop()

            indent = 0
            bullet = None
            if btype == "li":
                indent = int(base_px * 1.2)
                bullet = ("%d." % blk.get("index", 1)) if blk.get("ordered") else "•"
            elif blk.get("quote"):
                indent = int(base_px * 1.0)

            # 2) 贪心换行
            cur, cur_w, cur_pt = [], 0.0, self.base_pt
            if bullet:
                # 列表符号画进第一行行首，字号跟着内容走
                bpt = tokens[0][4] if tokens else self.base_pt
                bf = font_of(bpt)
                btok = bullet + " "
                cur.append((btok, False, False, False, bpt, False))
                cur_w = bf.getlength(btok)
            for tok, bold, italic, under, pt, quote in tokens:
                if tok == "\n":
                    lines.append(("text", cur_pt, cur))
                    cur, cur_w = [], 0.0
                    continue
                f = font_of(pt)
                w = f.getlength(tok)
                if cur_w + w > max(40, max_w - indent) and cur:
                    lines.append(("text", cur_pt, cur))
                    cur, cur_w = [], 0.0
                    if tok in " \t":
                        continue
                cur.append((tok, bold, italic, under, pt, quote))
                cur_w += w
                cur_pt = max(cur_pt, pt)
            if cur:
                lines.append(("text", cur_pt, cur))

            # 块间留白：标题上下更松一点
            gap = base_px * (0.65 if btype in HEADING_SCALE else 0.45)
            lines.append(("gap", int(gap), []))

        # 3) 按高度分页
        pages, y, page = [], 0, []
        for kind, size, content in lines:
            if kind == "hr":
                h = int(base_px * 0.6)
            elif kind == "gap":
                h = size
            else:
                # 连续换行会产生 content 为空的空行，高度按正文字号算
                pt_line = max([t[4] for t in content] or [self.base_pt])
                h = int(max(6, base_px * pt_line / float(self.base_pt)) * 1.55)
            if y + h > max_h and page:
                pages.append(page)
                page, y = [], 0
            page.append((kind, size, content, h))
            y += h
        pages.append(page)

        result = (pw, ph, margin, base_px, fonts, font_of, pages)
        self._cache[key] = result
        return result

    def page_count_prop(self):
        return max(1, len(self._layout(96)[-1]))

    def page_size_mm(self, index: int = 0):
        return self.paper_mm

    # -- 画
    def render(self, index: int = 0, dpi: float = 200):
        pw, ph, margin, base_px, fonts, font_of, pages = self._layout(dpi)
        img = Image.new("RGB", (pw, ph), "white")
        draw = ImageDraw.Draw(img)
        page = pages[index] if index < len(pages) else []
        y = margin
        for kind, size, content, h in page:
            if kind == "hr":
                draw.line([margin, y + h // 2, pw - margin, y + h // 2],
                          fill=(120, 120, 120), width=max(1, base_px // 24))
                y += h
                continue
            if kind == "gap":
                y += h
                continue

            # 本行的字号决定行高与缩进
            pt = max([t[4] for t in content], default=self.base_pt)
            f = font_of(pt)
            indent = 0
            if content and content[0][5]:        # quote
                indent = int(base_px * 1.0)

            x = margin + indent
            # 引用块画一条竖线
            if content and content[0][5]:
                draw.line([margin + int(base_px * 0.4), y,
                           margin + int(base_px * 0.4), y + h],
                          fill=(170, 170, 170), width=max(1, base_px // 20))

            for tok, bold, italic, under, tpt, _quote in content:
                tf = font_of(tpt)
                if italic:
                    x = self._draw_italic(img, x, y + h // 4, tok, tf, bold)
                else:
                    draw.text((x, y + h // 4), tok, font=tf, fill=(0, 0, 0),
                              stroke_width=(max(1, int(tpt / 12)) if bold else 0),
                              stroke_fill=(0, 0, 0))
                    x += tf.getlength(tok)
                if under:
                    draw.line([x - tf.getlength(tok), y + h // 4 + tf.size * 0.95,
                               x, y + h // 4 + tf.size * 0.95],
                              fill=(0, 0, 0), width=max(1, int(tf.size / 22)))
            y += h
        return img

    @staticmethod
    def _draw_italic(img, x, y, text, font, bold):
        """微软雅黑没有真斜体，用错切变换造一个。"""
        w = int(font.getlength(text)) + font.size * 2
        h = int(font.size * 1.8)
        pad = int(font.size * 0.4)
        pad_top = int(font.size * 0.25)
        small = Image.new("L", (w, h), 0)
        ImageDraw.Draw(small).text((0, pad_top), text, font=font, fill=255,
                                   stroke_width=(1 if bold else 0), stroke_fill=255)
        skewed = small.transform((w + pad, h), Image.AFFINE,
                                 (1, -0.22, pad, 0, 1, 0), Image.BILINEAR, fillcolor=0)
        # x 会一路累加 getlength 变成浮点，paste 只吃整数
        img.paste((0, 0, 0), (int(x), int(y) - pad_top), skewed)
        return int(x) + int(font.getlength(text))

    def _ensure_pages(self):
        self.page_count = self.page_count_prop()


def make_note_source(html: str, base_pt: int = 12) -> NoteSource:
    src = NoteSource(parse(html, base_pt), base_pt)
    src._ensure_pages()
    return src


def open_note(path: str, base_pt: int = 12) -> NoteSource:
    """从笔记的 .html 文件建 source（server 打印/预览时走这条路）。"""
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    return make_note_source(html, base_pt)
