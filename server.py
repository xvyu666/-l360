# -*- coding: utf-8 -*-
"""手机打印服务。

在局域网上起一个轻量 HTTP 服务，手机浏览器打开即可把图片 / PDF / Office /
手写文本发到这台电脑，由后台自动排版并静默打印到 EPSON L360。
"""
from __future__ import annotations

import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from PIL import Image

import printer_core as PC
import renderer as RD
import layout as LY
import wechat as WX

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
RUNTIME = os.path.join(HERE, "runtime")
JOBS = os.path.join(RUNTIME, "jobs")
LOG = os.path.join(RUNTIME, "server.log")
CONFIG = os.path.join(RUNTIME, "config.json")

MAX_UPLOAD = 200 * 1024 * 1024
PORT_START = 8760
VIRTUAL_PRINTERS = {
    "Microsoft Print to PDF", "Microsoft XPS Document Writer", "OneNote (Desktop)",
    "导出为WPS PDF", "Print to Evernote", "Adobe PDF", "Fax", "Microsoft Print To PDF",
}


def is_real_printer(name: str) -> bool:
    """统一走 printer_core 的**端口**判据，别在两套逻辑里各判一套。

    早年这里是按名字黑名单猜的，漏得多：名字里没有 pdf/adobe 的虚拟打印机照样混进来，
    而「FD 打印机」「得力打印机」这种名字又分不出真假。端口不会骗人。
    """
    return PC.is_real_printer(name)

QUALITY_DPI = {"draft": 200, "standard": 300, "high": 360}   # L360 原生 360dpi，贴着原生渲染最清晰
PREVIEW_EDGE = 320   # 预览图长边像素数（够手机高分屏 54x70 的缩略图用）
SUPPORTED_EXT = sorted(RD.IMAGE_EXT | RD.PDF_EXT | RD.OFFICE_EXT | RD.TEXT_EXT)

_lock = threading.RLock()
_jobs = {}          # id -> dict
_tasks = {}         # taskId -> dict
_config = {}


# ---------------------------------------------------------------- 基础设施


def ensure_dirs():
    for d in (RUNTIME, JOBS):
        os.makedirs(d, exist_ok=True)


def log(msg: str):
    line = "%s | %s\n" % (datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    try:
        sys.stdout.write(line)
        sys.stdout.flush()
    except Exception:
        pass


def load_config():
    global _config
    _config = {}
    try:
        if os.path.exists(CONFIG):
            with open(CONFIG, "r", encoding="utf-8") as f:
                _config = json.load(f)
    except Exception:
        _config = {}
    return _config


def save_config(patch: dict | None = None):
    if patch:
        _config.update(patch)
    try:
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(_config, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def _write_file(path: str, data: bytes):
    with open(path, "wb") as f:
        f.write(data)


# ---------------------------------------------------------------- 纸张几何

_ratio_cache = {}


def printable_ratio(printer: str, paper: str, orientation: int):
    """可打印区域占整张纸的比例（避开驱动 dpi 差异）。"""
    key = (printer, paper, orientation)
    if key in _ratio_cache:
        return _ratio_cache[key]
    import win32con
    import win32gui
    import win32print

    dm = PC.build_devmode(printer, paper, orientation)
    hdc = win32gui.CreateDC("WINSPOOL", printer, dm)
    try:
        gd = win32print.GetDeviceCaps
        hres = gd(hdc, win32con.HORZRES)
        vres = gd(hdc, win32con.VERTRES)
        fullw = gd(hdc, win32con.PHYSICALWIDTH) or hres
        fullh = gd(hdc, win32con.PHYSICALHEIGHT) or vres
    finally:
        win32gui.DeleteDC(hdc)
    val = (hres / float(fullw or 1), vres / float(fullh or 1))
    _ratio_cache[key] = val
    return val


def canvas_for(printer: str, paper: str, orientation: int, dpi: int):
    """给定纸张与方向，算出目标画布的像素尺寸与物理毫米数。"""
    pw_mm, ph_mm = PC.paper_mm(paper, orientation)
    rx, ry = printable_ratio(printer, paper, orientation)
    can_mm = (pw_mm * rx, ph_mm * ry)
    return (RD.mm_to_px(can_mm[0], dpi), RD.mm_to_px(can_mm[1], dpi)), can_mm


# ---------------------------------------------------------------- 任务管理


def new_job(name: str, kind: str, source_path: str, pages: int,
            text: str = "", font_pt: int = 12, size: int = 0, jid: str = None):
    # jid 允许由调用方传入：/api/upload 已经先把文件写进了以 jid 命名的文件夹，
    # 这里再另造一个 id 会出现「素材在 A 文件夹、meta 在 B 文件夹」的孤儿目录（还翻倍占磁盘）。
    jid = (jid or uuid.uuid4().hex[:10])[:10]
    folder = os.path.join(JOBS, jid)
    os.makedirs(folder, exist_ok=True)
    job = {
        "id": jid, "name": name, "kind": kind, "source": source_path,
        "pages": pages, "created": time.time(), "text": text,
        "fontPt": font_pt, "size": size, "folder": folder,
    }
    if not source_path:  # 纯文本
        tpath = os.path.join(folder, "note.txt")
        _write_file(tpath, (text or "").encode("utf-8"))
        job["source"] = tpath
    with _lock:
        _jobs[jid] = job
    try:
        with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False)
    except Exception:
        pass
    return job


def source_of(job: dict):
    if job["kind"] == "text":
        # 手写的字走内存渲染；上传的 .txt 和从微信取来的 .txt 是真实文件，必须读文件，
        # 否则 job["text"] 是空的，会打出一张白纸。
        if not (job.get("text") or "").strip() and job.get("source") and os.path.isfile(job["source"]):
            return RD.open_source(job["source"])
        return RD.make_text_source(job["text"], job.get("fontPt", 12))
    return RD.open_source(job["source"])


def prune_jobs(keep: int = 60):
    try:
        with _lock:
            items = sorted(_jobs.values(), key=lambda j: j["created"])
        for job in items[:-keep]:
            drop_job(job["id"])
    except Exception as e:
        log("prune failed: %s" % e)


def drop_job(jid: str):
    with _lock:
        job = _jobs.pop(jid, None)
    if not job:
        return
    folder = job.get("folder")
    if folder and os.path.isdir(folder):
        try:
            import shutil
            shutil.rmtree(folder, ignore_errors=True)
        except Exception:
            pass


# ---------------------------------------------------------------- 打印执行


def pick_orientation(printer, paper, src_mm, pref):
    """根据内容长宽比自动选纵/横打印，或按用户指定的方向。"""
    if pref == "portrait":
        return PC.DMORIENT_PORTRAIT
    if pref == "landscape":
        return PC.DMORIENT_LANDSCAPE
    p_p = PC.paper_mm(paper, PC.DMORIENT_PORTRAIT)
    p_l = PC.paper_mm(paper, PC.DMORIENT_LANDSCAPE)
    sw, sh = src_mm
    if sw <= 0 or sh <= 0:
        return PC.DMORIENT_PORTRAIT
    fit_p = min(p_p[0] / sw, p_p[1] / sh)
    fit_l = min(p_l[0] / sw, p_l[1] / sh)
    return PC.DMORIENT_LANDSCAPE if fit_l > fit_p else PC.DMORIENT_PORTRAIT


_TRIM_WHITE = 246.0        # 亮度高于此算空白（0-255）；留一点余量，淡灰表格线不会被误判成空白
_TRIM_MIN_AREA = 0.45      # 内容占比低于此就不动手 —— 签名居中、图表大片留白时按整页处理
_TRIM_MIN_SIZE = 8         # 裁完剩下不足 8px 说明判定不可靠


def content_bbox(img, tol: float = _TRIM_WHITE, min_area: float = _TRIM_MIN_AREA):
    """找出页面里真正有内容的矩形边界。

    Word / Excel / PPT / PDF 的页面自带一圈页边距，印出来本来就是纯白的。
    「按边距重排」要先量出这圈白边有多宽，才能把真正的内容按新的边距放大。

    返回 (x0, y0, x1, y1)；返回 None 表示「不要裁」——
    页面本来就铺满、内容占比过低、或根本是全空白，都按整页处理，免得放大失控。
    """
    try:
        mask = img.convert("L").point(lambda v: 0 if v > tol else 255)
        bb = mask.getbbox()
    except Exception:
        return None
    del mask
    if not bb:
        return None
    x0, y0, x1, y1 = bb
    w, h = img.width, img.height
    cw, ch = x1 - x0, y1 - y0
    if cw <= 0 or ch <= 0 or w <= 0 or h <= 0:
        return None
    if (cw * ch) / float(w * h) < min_area:
        return None                       # 内容太稀疏，多半是刻意留白，别乱放大
    if cw >= w * 0.995 and ch >= h * 0.995:
        return None                       # 本来就铺满，没什么可剥的
    return bb


def apply_margins(page_img, canvas_px, margin, dpi, reflow: bool = False):
    """把排好的一页放进「页边距内框」，等比缩放，绝不拉伸。

    reflow=False（原来的行为）：整页连它自带的页边距一起缩进边距框。
        结果就是边距套边距，内容被压小 —— 这是之前发现的问题。

    reflow=True（按边距重排）：先剥掉文件自带的白边，再把真正的内容等比放大到边距框。
        内容到纸边的距离就等于这里设的边距；边距调小，内容跟着变大。
    """
    try:
        mt = max(0.0, float(margin.get("t", 0))) if margin else 0.0
        mr = max(0.0, float(margin.get("r", 0))) if margin else 0.0
        mb = max(0.0, float(margin.get("b", 0))) if margin else 0.0
        ml = max(0.0, float(margin.get("l", 0))) if margin else 0.0
    except (TypeError, ValueError, AttributeError):
        return page_img

    inner = page_img
    trimmed = False
    if reflow:
        bb = content_bbox(inner)
        if bb:
            cand = inner.crop(bb)
            if cand.width >= _TRIM_MIN_SIZE and cand.height >= _TRIM_MIN_SIZE:
                inner, trimmed = cand, True

    if mt + mr + mb + ml <= 0.01:
        # 贴边：重排模式下白边已经剥掉了，内容要放回满纸；其余情况保持原样
        if not trimmed:
            return page_img

    px = lambda mm: int(round(mm / 25.4 * dpi))
    bw = canvas_px[0] - px(ml) - px(mr)
    bh = canvas_px[1] - px(mt) - px(mb)
    if bw < 20 or bh < 20:          # 边距大到放不下东西，宁可不打也别打坏
        return page_img

    scale = min(bw / float(inner.width), bh / float(inner.height))
    w = max(1, int(round(inner.width * scale)))
    h = max(1, int(round(inner.height * scale)))
    if (w, h) != inner.size:
        inner = inner.resize((w, h), Image.LANCZOS)
    x0, y0 = px(ml), px(mt)
    out = Image.new("RGB", canvas_px, "white")
    out.paste(inner, (x0 + (bw - w) // 2, y0 + (bh - h) // 2))
    return out


def filter_pages(n, duplex, phase):
    """手动双面：按奇偶拆成两轮。"""
    seq = list(range(1, n + 1))
    if duplex == "off":
        return seq
    odd = [p for p in seq if p % 2 == 1]
    even = [p for p in seq if p % 2 == 0]
    if duplex == "short":
        even = list(reversed(even))
    return odd if phase == "odd" else even


# ---------------------------------------------------------------- 原格式直印


NATIVE_TIMEOUT = 300


def native_phase(duplex: str, phase: str) -> str:
    """手动双面时告诉 Word 这轮只打奇数页还是偶数页。"""
    if duplex == "off" or phase == "all":
        return "all"
    return "odd" if phase == "odd" else "even"


def print_native(src_path: str, printer: str, copies: int, phase: str = "all"):
    """让 Office 用自己的打印引擎出纸。返回 (ok, error)。"""
    cmd = [sys.executable, os.path.join(HERE, "native.py"),
           os.path.abspath(src_path), printer, str(copies), phase]
    try:
        proc = subprocess.run(cmd, timeout=NATIVE_TIMEOUT, capture_output=True, text=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return False, "打印超时（Office 没有响应）"
    except Exception as e:
        return False, str(e)
    if proc.returncode != 0:
        msg = (proc.stderr or "").strip()[-300:] or (proc.stdout or "").strip()[-200:]
        return False, msg or "Office 打印失败"
    return True, ""


def run_native_task(task_id: str, jobs: list, options: dict, printer: str) -> bool:
    """原格式直印：Word/Excel/PPT 交给 Office 自己打，版式与电脑上 Ctrl+P 一致。

    返回是否真的走了直印（False 表示这批里没有 Office 文档，调用方应改走渲染模式）。
    """
    task = _tasks[task_id]
    copies = max(1, min(int(options.get("copies", 1)), 99))
    duplex = options.get("duplex", "off")
    phase = options.get("phase", "all")

    task["total"] = len(jobs)
    task["state"] = "working"
    want = native_phase(duplex, phase)
    done = 0
    for job in jobs:
        ok, err = print_native(job["source"], printer, copies, want)
        if not ok:
            log("native print failed: %s / %s" % (job["name"], err))
            task["state"] = "error"
            task["error"] = "「%s」原格式打印失败：%s" % (job["name"], err)
            return True
        done += 1
        task["done"] = done
    task["pages"] = done
    task["done"] = done
    task["state"] = "done"
    log("native printed ok: task=%s files=%d copies=%d duplex=%s phase=%s" %
        (task_id, done, copies, duplex, phase))
    return True


def run_print_task(task_id: str, job_ids: list, options: dict, printer: str):
    """后台执行打印：一边渲染一边分批下发，内存可控，前台可查进度。"""
    task = _tasks[task_id]
    sources = []
    try:
        # 原格式直印优先：Office 文档交给 Office 自己打，版式 100% 保持原样。
        # 手动双面时不走直印：那要靠 Office 的「只打奇数页」开关，效果没法在这台机器上
        # 稳定验证，而渲染模式的奇偶页是我们自己切分的，确定可靠 —— 宁可牺牲一点版式。
        hand_duplex = options.get("duplex", "off") != "off" and options.get("phase", "all") != "all"
        if options.get("mode") == "native" and not hand_duplex:
            with _lock:
                picked = [_jobs[j] for j in job_ids if j in _jobs]
            office_jobs = [j for j in picked if j.get("kind") == "office"]
            if office_jobs:
                # 直印失败不静默改版式（那会跟用户以为的「原样」不符），直接报错让他切回排版模式
                run_native_task(task_id, office_jobs, options, printer)
                if task["state"] == "error":
                    return
                # 同批里还有图片/PDF 的话，剩下的走渲染模式继续打
                rest = [j["id"] for j in picked if j.get("kind") != "office"]
                if not rest:
                    return
                job_ids = rest
        quality = options.get("quality", "standard")
        dpi = QUALITY_DPI.get(quality, 200)
        paper = options.get("paper", PC.DEFAULT_PAPER)
        # 换打印机后原来的纸张可能根本不被支持。硬发一个不认识的 PaperSize，
        # 驱动多半悄悄忽略掉，结果就是打出来的尺寸跟预览完全对不上。
        # 这里查一下驱动真正认哪些纸，不支持就降级到它肯定支持的，免得白费一张纸。
        _sup = None
        try:
            _sup = PC.supported_papers(printer)
        except Exception:
            pass
        if _sup and paper not in _sup:
            _alt = "A4" if "A4" in _sup else sorted(_sup)[0]
            log("纸张 %s 不在「%s」支持列表内，改用 %s（该机支持：%s）"
                % (paper, printer, _alt, ",".join(sorted(_sup))))
            paper = _alt
        layout = options.get("layout", "fit")
        reflow = (layout == "reflow")
        gray = options.get("color", "color") == "mono"
        copies = max(1, min(int(options.get("copies", 1)), 99))
        duplex = options.get("duplex", "off")
        phase = options.get("phase", "all")
        orient_pref = options.get("orientation", "auto")
        render_base = min(max(dpi * 1.5, 150), 400)
        # 重排要把剥掉白边的内容放大贴回纸上，画布得用和源图同级的分辨率承接，
        # 否则等于先把源图降采样再拉大，放大出来的那一截全是糊的
        canvas_dpi = render_base if reflow else dpi
        render_dpi = min(max(canvas_dpi * 1.5, 150), 400)
        title = "手机打印 %s" % datetime.now().strftime("%H:%M")

        pages_info = []
        for jid in job_ids:
            with _lock:
                job = _jobs.get(jid)
            if not job:
                continue
            try:
                src = source_of(job)
            except Exception as e:
                task["error"] = "打开「%s」失败：%s" % (job["name"], e)
                task["state"] = "error"
                return
            sources.append(src)
            for i in range(src.page_count):
                pages_info.append((src, i, src.page_size_mm(i), job["name"]))

        if not pages_info:
            task["error"] = "没有可打印的内容"
            task["state"] = "error"
            return

        seq_idx = filter_pages(len(pages_info), duplex, phase)
        if not seq_idx:
            task["error"] = "这个阶段没有要打印的页面"
            task["state"] = "error"
            return

        task["total"] = len(seq_idx) * copies
        task["state"] = "working"

        batch, batch_ori, printed = [], None, 0
        for _copy in range(copies):
            for pnum in seq_idx:
                src, i, src_mm, _name = pages_info[pnum - 1]
                ori = pick_orientation(printer, paper, src_mm, orient_pref)
                if batch and batch_ori is not None and ori != batch_ori:
                    PC.print_images(printer, batch, paper, batch_ori, title)
                    printed += len(batch)
                    task["done"] = printed
                    batch = []
                batch_ori = ori
                canvas_px, canvas_mm = canvas_for(printer, paper, ori, canvas_dpi)
                # 高分辨率画布上一页能占到 40MB，大批攒着容易爆内存
                cap = 3 if canvas_px[0] * canvas_px[1] > 9_000_000 else 6
                base = src.render(i, render_dpi)
                batch.append(apply_margins(
                    RD.compose_page(base, src_mm, canvas_px, canvas_mm, layout, gray),
                    canvas_px, options.get("margin"), canvas_dpi, reflow))
                del base
                if len(batch) >= cap:
                    PC.print_images(printer, batch, paper, batch_ori, title)
                    printed += len(batch)
                    task["done"] = printed
                    batch = []
        if batch:
            PC.print_images(printer, batch, paper, batch_ori, title)
            printed += len(batch)
            batch = []

        task["pages"] = printed
        task["done"] = printed
        task["state"] = "done"
        log("printed ok: task=%s pages=%d paper=%s duplex=%s phase=%s" %
            (task_id, printed, paper, duplex, phase))
    except Exception as e:
        task["state"] = "error"
        task["error"] = str(e)
        log("print failed: %s\n%s" % (e, traceback.format_exc()))
    finally:
        for s in sources:
            try:
                s.close()
            except Exception:
                pass



def run_compose_task(task_id: str, pages_spec: list, options: dict, printer: str):
    """按前端编排好的版面逐页合成并打印。

    一页一页来而不是一次性全渲出来：长截图切成几十页时，全放内存里会很危险。
    """
    task = _tasks[task_id]
    try:
        quality = options.get("quality", "standard")
        dpi = QUALITY_DPI.get(quality, 200)
        paper = options.get("paper", PC.DEFAULT_PAPER)
        gray = options.get("color", "color") == "mono"
        copies = max(1, min(int(options.get("copies", 1)), 99))
        ori_s = options.get("orientation", "portrait")
        ori = PC.DMORIENT_LANDSCAPE if ori_s == "landscape" else PC.DMORIENT_PORTRAIT
        canvas_px, _canvas_mm = canvas_for(printer, paper, ori, dpi)
        title = "手机排版打印 %s" % datetime.now().strftime("%H:%M")

        def resolver(jid):
            with _lock:
                job = _jobs.get(jid)
            return source_of(job) if job else None

        task["total"] = len(pages_spec) * copies
        task["state"] = "working"

        printed = 0
        for _c in range(copies):
            batch = []
            for spec in pages_spec:
                pages = LY.compose_pages(resolver, [spec], canvas_px, gray, log_fn=log)
                batch.extend(pages)
                if len(batch) >= 6:
                    PC.print_images(printer, batch, paper, ori, title)
                    printed += len(batch)
                    task["done"] = printed
                    for p in batch:
                        p.close()
                    batch = []
            if batch:
                PC.print_images(printer, batch, paper, ori, title)
                printed += len(batch)
                task["done"] = printed
                for p in batch:
                    p.close()

        task["pages"] = printed
        task["done"] = printed
        task["state"] = "done"
        log("compose printed ok: task=%s pages=%d paper=%s ori=%s" %
            (task_id, printed, paper, ori_s))
    except Exception as e:
        task["state"] = "error"
        task["error"] = str(e)
        log("compose failed: %s\n%s" % (e, traceback.format_exc()))


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "PrintBridge/1.0"
    protocol_version = "HTTP/1.1"

    def address_string(self):
        return self.client_address[0]

    def log_message(self, fmt, *args):
        pass

    # -- helpers
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _bytes(self, data: bytes, ctype: str, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    STATIC_MIME = {
        ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".ico": "image/x-icon",
        ".json": "application/json; charset=utf-8",
    }

    def serve_static(self, rel: str):
        """从 web/ 目录取静态资源。必须挡住 ../ 穿越。"""
        rel = os.path.normpath(rel.replace("\\", "/")).lstrip("/")
        if rel.startswith("..") or rel.startswith("~"):
            return self._json({"error": "bad path"}, 403)
        fp = os.path.abspath(os.path.join(WEB, rel))
        root = os.path.abspath(WEB)
        if not (fp == root or fp.startswith(root + os.sep)):
            return self._json({"error": "bad path"}, 403)
        if not os.path.isfile(fp):
            return self._json({"error": "not found"}, 404)
        ext = os.path.splitext(fp)[1].lower()
        mime = self.STATIC_MIME.get(ext, "application/octet-stream")
        with open(fp, "rb") as f:
            return self._bytes(f.read(), mime)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("文件太大")
        return self.rfile.read(n) if n else b""

    def _redirect_root(self):
        self.send_response(302)
        self.send_header("Location", "/")
        self.end_headers()

    # -- GET
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                with open(os.path.join(WEB, "index.html"), "rb") as f:
                    return self._bytes(f.read(), "text/html; charset=utf-8")
            if path == "/favicon.svg":
                with open(os.path.join(WEB, "favicon.svg"), "rb") as f:
                    return self._bytes(f.read(), "image/svg+xml")
            if path.startswith("/static/"):
                return self.serve_static(path[len("/static/"):])
            if path == "/health":
                return self._json({"ok": True, "ts": time.time()})
            if path == "/api/info":
                return self.api_info()
            if path == "/api/state":
                printer = _config.get("printer") or PC.default_printer()
                st = PC.printer_state(printer)
                st["papers"] = [{"key": k, "label": v["label"], "short": v["short"]}
                                for k, v in PC.PAPERS.items()]
                st["ext"] = SUPPORTED_EXT
                return self._json(st)
            if path == "/api/canvas":
                # 前端编辑器要按真实画布比例摆放条目，这里给出精确像素尺寸
                q = parse_qs(urlparse(self.path).query)
                qpaper = (q.get("paper") or [PC.DEFAULT_PAPER])[0]
                paper = qpaper if qpaper in PC.PAPERS else PC.DEFAULT_PAPER
                qual = (q.get("quality") or ["standard"])[0]
                dpi = QUALITY_DPI.get(qual, 200)
                ori_s = (q.get("orientation") or ["portrait"])[0]
                ori = PC.DMORIENT_LANDSCAPE if ori_s == "landscape" else PC.DMORIENT_PORTRAIT
                printer = _config.get("printer") or PC.default_printer()
                canvas_px, canvas_mm = canvas_for(printer, paper, ori, dpi)
                return self._json({
                    "w": canvas_px[0], "h": canvas_px[1],
                    "mm": [round(canvas_mm[0], 2), round(canvas_mm[1], 2)],
                    "paper": paper, "orientation": ori_s, "quality": qual,
                    "aspect": canvas_px[0] / float(canvas_px[1]),
                })
            if path == "/api/jobs":
                with _lock:
                    items = sorted(_jobs.values(), key=lambda j: j["created"])
                return self._json({"jobs": [public(j) for j in items][-40:]})
            m = re.match(r"^/api/preview/([0-9a-f]{8,12})/(\d+)$", path)
            if m:
                return self.api_preview(m.group(1), int(m.group(2)))
            m = re.match(r"^/api/source/([0-9a-f]{8,12})/(\d+)$", path)
            if m:
                return self.api_source(m.group(1), int(m.group(2)))
            m = re.match(r"^/api/progress/([0-9a-f\-]{6,40})$", path)
            if m:
                t = _tasks.get(m.group(1))
                return self._json(t or {"state": "missing"})
            if path == "/api/wechat/status":
                return self.api_wechat_status()
            if path == "/api/wechat/files":
                return self.api_wechat_files()
            if path == "/api/wechat/images":
                return self.api_wechat_images()
            if path == "/api/wechat/sessions":
                return self.api_wechat_sessions()
            m = re.match(r"^/api/wechat/thumb/([0-9a-f]{16})$", path)
            if m:
                return self.api_wechat_thumb(m.group(1))
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            log("GET %s failed: %s" % (path, e))
            return self._json({"error": str(e)}, 500)

    # -- POST
    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/text":
                data = json.loads(self._body().decode("utf-8") or "{}")
                text = (data.get("text") or "").strip()
                if not text:
                    return self._json({"error": "内容是空的"}, 400)
                font_pt = int(data.get("fontPt") or 12)
                src = RD.make_text_source(text, font_pt)
                pages = src.page_count
                src.close()
                name = re.sub(r"\s+", " ", text[:24]) + ("…" if len(text) > 24 else "")
                job = new_job("随手记：" + name, "text", "", pages, text=text,
                              font_pt=font_pt, size=len(text.encode("utf-8")))
                log("new text job %s pages=%d" % (job["id"], pages))
                return self._json({"job": public(job)})

            if path == "/api/upload":
                raw_name = unquote(self.headers.get("X-File-Name", "") or "unnamed")
                name = os.path.basename(raw_name.replace("\\", "/")) or "unnamed"
                data = self._body()
                if not data:
                    return self._json({"error": "没收到文件"}, 400)
                ext = os.path.splitext(name)[1].lower().lstrip(".")
                if RD.classify(name) == "unknown":
                    return self._json({"error": "不支持的格式：.%s" % (ext or "未知")}, 415)
                jid = uuid.uuid4().hex[:10]
                folder = os.path.join(JOBS, jid)
                os.makedirs(folder, exist_ok=True)
                safe = re.sub(r'[\\/:*?"<>|]+', "_", name)
                spath = os.path.join(folder, safe)
                _write_file(spath, data)
                try:
                    src = RD.open_source(spath)
                    pages = src.page_count
                    src.close()
                except Exception as e:
                    import shutil
                    shutil.rmtree(folder, ignore_errors=True)
                    return self._json({"error": str(e)}, 415)
                job = new_job(safe, RD.classify(spath), spath, pages, size=len(data), jid=jid)
                log("new job %s name=%s ext=%s pages=%d size=%d" %
                    (job["id"], safe, ext, pages, len(data)))
                return self._json({"job": public(job)})

            if path == "/api/print":
                data = json.loads(self._body().decode("utf-8") or "{}")
                job_ids = data.get("jobIds") or []
                options = data.get("options") or {}
                printer = _config.get("printer") or PC.default_printer()
                if not job_ids:
                    return self._json({"error": "没有选择文件"}, 400)
                tid = uuid.uuid4().hex[:12]
                task = {"state": "queued", "done": 0, "total": 0, "pages": 0,
                        "error": None, "started": time.time()}
                _tasks[tid] = task
                th = threading.Thread(target=run_print_task,
                                      args=(tid, job_ids, options, printer), daemon=True)
                th.start()
                return self._json({"taskId": tid})

            if path == "/api/compose":
                data = json.loads(self._body().decode("utf-8") or "{}")
                pages_spec = data.get("pages") or []
                if not pages_spec:
                    return self._json({"error": "版面是空的"}, 400)
                printer = (data.get("printer")
                           or _config.get("printer")
                           or PC.default_printer())
                tid = uuid.uuid4().hex[:12]
                task = {"state": "queued", "done": 0, "total": len(pages_spec),
                        "pages": 0, "error": None, "started": time.time()}
                _tasks[tid] = task
                th = threading.Thread(target=run_compose_task,
                                      args=(tid, pages_spec, data.get("options") or {},
                                            printer), daemon=True)
                th.start()
                return self._json({"taskId": tid})

            if path == "/api/testpage":
                printer = _config.get("printer") or PC.default_printer()
                tid = uuid.uuid4().hex[:12]
                task = {"state": "queued", "done": 0, "total": 1, "pages": 0,
                        "error": None, "started": time.time()}
                _tasks[tid] = task

                def _test():
                    try:
                        dpi = 200
                        canvas_px, canvas_mm = canvas_for(printer, PC.DEFAULT_PAPER,
                                                          PC.DMORIENT_PORTRAIT, dpi)
                        img = Image.new("RGB", canvas_px, "white")
                        from PIL import ImageDraw
                        d = ImageDraw.Draw(img)
                        font = RD.load_font(int(canvas_px[0] * 0.045))
                        d.text((80, 120), "手机打印联调测试", font=font, fill=(0, 0, 0))
                        d.rectangle([80, 300, canvas_px[0] - 80, 420], outline=(0, 0, 0), width=6)
                        d.text((80, 460), datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                               font=font, fill=(80, 80, 80))
                        task["state"] = "working"
                        PC.print_images(printer, [img], PC.DEFAULT_PAPER,
                                        PC.DMORIENT_PORTRAIT, title="手机打印测试页")
                        task["state"] = "done"
                        task["pages"] = 1
                    except Exception as e:
                        task["state"] = "error"
                        task["error"] = str(e)

                threading.Thread(target=_test, daemon=True).start()
                return self._json({"taskId": tid})

            if path == "/api/reprint":
                data = json.loads(self._body().decode("utf-8") or "{}")
                return self._json({"jobIds": data.get("jobIds") or []})

            if path == "/api/wechat/import":
                return self.api_wechat_import()

            if path == "/api/printer":
                data = json.loads(self._body().decode("utf-8") or "{}")
                name = (data.get("printer") or "").strip()
                if name:
                    save_config({"printer": name})
                    _ratio_cache.clear()
                return self._json({"printer": _config.get("printer")})

            return self._json({"error": "not found"}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:
            log("POST %s failed: %s\n%s" % (path, e, traceback.format_exc()))
            return self._json({"error": str(e)}, 500)

    def do_DELETE(self):
        path = urlparse(self.path).path
        m = re.match(r"^/api/job/([0-9a-f]{8,12})$", path)
        if m:
            drop_job(m.group(1))
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)

    # -- api impl
    def api_info(self):
        # 带上每台机器支持的纸张，前端才能把打不了的选项藏起来
        detail = PC.real_printers()
        printers = [d["name"] for d in detail] or PC.list_printers()
        cur = _config.get("printer") or PC.default_printer()
        if cur not in printers and printers:
            cur = printers[0]
        return self._json({
            "printer": cur,
            "printers": printers,
            "printerDetail": detail,
            "host": socket.gethostname(),
            "ip": local_ip(),
            "port": self.server.server_port,
            "version": "1.0",
        })

    def api_preview(self, jid, index):
        with _lock:
            job = _jobs.get(jid)
        if not job:
            return self._json({"error": "任务不存在"}, 404)
        q = urlparse(self.path).query or ""
        paper = PC.DEFAULT_PAPER
        layout = "fit"
        gray = False
        orientation = "auto"
        edge = PREVIEW_EDGE
        margin = {}
        for kv in q.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                v = unquote(v)
                if k == "paper" and v in PC.PAPERS:
                    paper = v
                elif k == "layout":
                    layout = v
                elif k == "color":
                    gray = (v == "mono")
                elif k == "orientation":
                    orientation = v
                elif k == "w":
                    # 编辑器要高清图做白边检测 / 放大查看，允许临时提高分辨率
                    try:
                        edge = max(64, min(int(v), 2400))
                    except ValueError:
                        pass
                elif k in ("mt", "mr", "mb", "ml"):
                    try:
                        margin[k[1]] = max(0.0, min(float(v), 100.0))  # mt->t，与 apply_margins 对齐
                    except ValueError:
                        pass
        try:
            src = source_of(job)
            try:
                i = min(index, src.page_count - 1)
                src_mm = src.page_size_mm(i)
                printer = _config.get("printer") or PC.default_printer()
                ori = pick_orientation(printer, paper, src_mm, orientation)
                target_mm = PC.paper_mm(paper, ori)
                long_inch = max(target_mm) / RD.MM_PER_INCH
                prev_dpi = max(30.0, min(edge / long_inch, 200.0 if edge <= PREVIEW_EDGE else 400.0))
                reflow = (layout == "reflow")
                # 和打印路径同口径：重排要放大内容，源图多给 1.5 倍，预览才不会比实际出纸更糊
                src_dpi = min(max(prev_dpi * 1.5, 60.0), 400.0) if reflow else prev_dpi
                canvas_px, canvas_mm = canvas_for(printer, paper, ori, prev_dpi)
                img = src.render(i, src_dpi)
                out = RD.compose_page(img, src_mm, canvas_px, canvas_mm, layout, gray)
                out = apply_margins(out, canvas_px, margin, prev_dpi, reflow)
                buf = io.BytesIO()
                out.convert("RGB").save(buf, "JPEG", quality=72)
                return self._bytes(buf.getvalue(), "image/jpeg")
            finally:
                src.close()
        except Exception as e:
            log("preview failed: %s" % e)
            return self._json({"error": str(e)}, 500)


    def api_source(self, jid, index):
        """给排版编辑器返回素材原图：保持原始宽高比，不套纸张画布。

        &w= 指定宽度（默认 900），用于在手机上做白边检测和放大查看。
        """
        with _lock:
            job = _jobs.get(jid)
        if not job:
            return self._json({"error": "任务不存在"}, 404)
        q = parse_qs(urlparse(self.path).query)
        try:
            want = int((q.get("w") or ["900"])[0])
        except (ValueError, TypeError):
            want = 900
        want = max(64, min(want, 2400))
        try:
            src = source_of(job)
            try:
                i = max(0, min(index, src.page_count - 1))
                mm = src.page_size_mm(i)
                long_mm = max(mm) or 200.0
                dpi = max(30.0, min(want / (long_mm / RD.MM_PER_INCH), 400.0))
                img = src.render(i, dpi).convert("RGB")
                if img.width > want:
                    h = max(1, int(round(img.height * want / float(img.width))))
                    img = img.resize((want, h), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=86)
                return self._bytes(buf.getvalue(), "image/jpeg")
            finally:
                src.close()
        except Exception as e:
            log("source render failed: %s" % e)
            return self._json({"error": str(e)}, 500)


    # -- 微信文件（自助打印）

    def api_wechat_status(self):
        """微信数据目录在不在、微信开着没有——决定前端要不要提示用户先登录。"""
        try:
            st = WX.status()
        except Exception as e:
            log("wechat status failed: %s" % e)
            return self._json({"ok": False, "error": str(e), "running": False, "roots": []})
        st["ts"] = time.time()
        return self._json(st)

    def api_wechat_files(self):
        q = parse_qs(urlparse(self.path).query)
        try:
            hours = max(0, min(int((q.get("hours") or ["72"])[0]), 24 * 365))
        except ValueError:
            hours = 72
        try:
            limit = max(1, min(int((q.get("limit") or ["120"])[0]), 300))
        except ValueError:
            limit = 120
        kw = (q.get("q") or [""])[0]
        force = (q.get("force") or [""])[0] == "1"
        try:
            since = float((q.get("since") or ["0"])[0])
        except ValueError:
            since = 0.0
        try:
            files = WX.scan(hours=hours, limit=limit, keyword=kw, force=force, since=since)
        except Exception as e:
            log("wechat scan failed: %s" % e)
            return self._json({"ok": False, "error": str(e), "files": []})
        return self._json({
            "ok": True,
            "count": len(files),
            "files": [WX.public(f) for f in files],
            "ts": time.time(),
        })

    def api_wechat_images(self):
        """微信里的明文图片（聊天缩略图 / 收藏高清图 / 迁移图）。"""
        q = parse_qs(urlparse(self.path).query)

        def _int(name, default, lo, hi):
            try:
                return max(lo, min(int((q.get(name) or [default])[0]), hi))
            except ValueError:
                return default

        try:
            since = float((q.get("since") or ["0"])[0])
        except ValueError:
            since = 0.0
        try:
            items = WX.scan_images(
                hours=_int("hours", 72, 0, 24 * 365),
                limit=_int("limit", 120, 1, 300),
                session=(q.get("session") or [""])[0],
                since=since,
                min_edge=_int("minpx", WX._MIN_EDGE, 0, 4000),
            )
        except Exception as e:
            log("wechat scan_images failed: %s" % e)
            return self._json({"ok": False, "error": str(e), "images": []})
        return self._json({
            "ok": True,
            "count": len(items),
            "images": [WX.public_image(i) for i in items],
            "filehelper": WX.FILEHELPER_ID,
            "ts": time.time(),
        })

    def api_wechat_sessions(self):
        """有图片的会话列表，前端用来做来源筛选。"""
        try:
            sess = WX.image_sessions()
        except Exception as e:
            log("wechat image_sessions failed: %s" % e)
            return self._json({"ok": False, "error": str(e), "sessions": []})
        return self._json({"ok": True, "sessions": sess,
                           "filehelper": WX.FILEHELPER_ID})

    def api_wechat_thumb(self, fid):
        """只给图片出缩略图。文档类用前端的类型色块，避免为一张缩略图去启 Office 转 PDF。"""
        path = WX.resolve(fid)
        if not path:
            return self._json({"error": "文件不存在"}, 404)
        # 收藏缓存里的图片没有扩展名，classify 认不出来，得先问 wechat 模块
        if not WX.is_image(fid) and RD.classify(path) != "image":
            return self._json({"error": "不是图片"}, 404)
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((240, 320), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=76)
            return self._bytes(buf.getvalue(), "image/jpeg")
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def api_wechat_import(self):
        """把微信里的文件复制一份进作业目录，之后走正常打印流程（原文件绝不动）。"""
        import shutil
        data = json.loads(self._body().decode("utf-8") or "{}")
        ids = data.get("ids") or []
        if not ids:
            return self._json({"error": "没有选择文件"}, 400)
        jobs, errs = [], []
        for fid in ids[:30]:
            src = WX.resolve(fid)
            if not src or not os.path.isfile(src):
                errs.append("文件已经不在了（可能被微信清理）")
                continue
            name = os.path.basename(src)
            if WX.is_image(fid):
                # 缓存图文件名是一串 hash，换成「会话_时间_像素」才认得出是哪张
                name = WX.image_name(fid, src)
            elif RD.classify(name) == "unknown":
                errs.append("%s：不支持的格式" % name)
                continue
            jid = uuid.uuid4().hex[:10]
            folder = os.path.join(JOBS, jid)
            os.makedirs(folder, exist_ok=True)
            safe = re.sub(r'[\\/:*?"<>|]+', "_", name)
            dst = os.path.join(folder, safe)
            try:
                shutil.copy2(src, dst)
                s = RD.open_source(dst)
                pages = s.page_count
                s.close()
            except Exception as e:
                shutil.rmtree(folder, ignore_errors=True)
                errs.append("%s：%s" % (name, e))
                continue
            job = new_job(safe, RD.classify(dst), dst, pages,
                          size=os.path.getsize(dst), jid=jid)
            jobs.append(public(job))
        if jobs:
            log("wechat import: %d file(s) -> %s" % (len(jobs), ",".join(j["name"] for j in jobs[:3])))
        return self._json({"jobs": jobs, "errors": errs})


def public(job: dict):
    return {
        "id": job["id"], "name": job["name"], "kind": job["kind"],
        "pages": job["pages"], "size": job.get("size", 0),
        "created": job["created"], "fontPt": job.get("fontPt", 12),
    }


def already_running(port: int) -> bool:
    """端口上是不是已经有一个本服务在跑。

    Windows 上 SO_REUSEADDR 允许两个进程绑同一个端口而不报错，结果是请求被随机
    分给其中一个——旧代码的实例会返回 404，看起来像「功能时灵时不灵」。所以开跑
    之前先探一下，发现是自己人就别再起第二个。
    """
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=1.2) as r:
            return b"ok" in r.read(200)
    except Exception:
        return False


def start(port: int | None = None):
    ensure_dirs()
    load_config()
    port = port or int(os.environ.get("PRINTSERVER_PORT") or PORT_START)
    for attempt in range(6):
        if already_running(port):
            msg = ("端口 %d 上已经有一个打印服务在跑了，直接用它就行：http://%s:%d\n"
                   "（要重启的话，先关掉那个最小化的黑色窗口）" % (port, local_ip(), port))
            print(msg)
            log("already running on port %d, exit" % port)
            return
        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
            break
        except OSError:
            port += 1
    else:
        raise SystemExit("端口都被占用了")
    ip = local_ip()
    banner = [
        "",
        "  ============================================================",
        "   手机打印服务已启动",
        "   手机连同一个 WiFi 后，浏览器打开：",
        "",
        "       http://%s:%d" % (ip, port),
        "",
        "   打印机：%s" % (_config.get("printer") or PC.default_printer()),
        "   日志：%s" % LOG,
        "   按 Ctrl+C 停止",
        "  ============================================================",
        "",
    ]
    log("\n".join(banner))
    print("\n".join(banner))
    try:
        httpd.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    start()
