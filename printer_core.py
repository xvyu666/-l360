# -*- coding: utf-8 -*-
"""Windows GDI 打印核心。

不依赖任何第三方软件（不装 SumatraPDF / Acrobat / Foxit 也能用）。
做法：程序先把每一页渲染成高清位图，再一次性放进一个打印作业里，
用 FromHandle 拿到 PyCDC 后按页画到打印机 DC 上，全程无窗口、无弹窗。
"""
from __future__ import annotations

import ctypes
import io
import re
import threading
from ctypes import wintypes

import win32con
import win32gui
import win32print
import win32ui
from PIL import Image

# ---------------------------------------------------------------- 常量

DM_ORIENTATION = 0x00000001
DM_PAPERSIZE = 0x00000002
DM_PAPERLENGTH = 0x00000004
DM_PAPERWIDTH = 0x00000008
DM_COPIES = 0x00000100
DM_COLOR = 0x00000800
DM_PRINTQUALITY = 0x00000400

DMORIENT_PORTRAIT = 1
DMORIENT_LANDSCAPE = 2
DMCOLOR_COLOR = 2
DMCOLOR_MONOCHROME = 1

DIB_RGB_COLORS = 0
SRCCOPY = 0x00CC0020

PAPERS = {
    "A4": {"code": 9, "w": 2100, "l": 2970, "label": "A4 210×297mm", "short": "A4"},
    "A5": {"code": 11, "w": 1480, "l": 2100, "label": "A5 148×210mm", "short": "A5"},
    "A6": {"code": 70, "w": 1050, "l": 1480, "label": "A6 105×148mm", "short": "A6"},
    "B5": {"code": 13, "w": 1820, "l": 2570, "label": "B5 182×257mm", "short": "B5"},
    "PHOTO_4R": {"code": 285, "w": 1016, "l": 1524, "label": "照片纸 10×15cm (4R)", "short": "4R"},
    "PHOTO_5R": {"code": 284, "w": 1270, "l": 1780, "label": "照片纸 13×18cm (5R)", "short": "5R"},
    "LETTER": {"code": 1, "w": 2159, "l": 2794, "label": "信纸 Letter 8.5×11in", "short": "Letter"},
    "LEGAL": {"code": 5, "w": 2159, "l": 3556, "label": "Legal 8.5×14in", "short": "Legal"},
}

DEFAULT_PAPER = "A4"

# PRINTER_STATUS_* 数值（wingdi.h）
STATUS_PAUSED = 0x00000001
STATUS_ERROR = 0x00000002
STATUS_PAPER_JAM = 0x00000008
STATUS_PAPER_OUT = 0x00000010
STATUS_PAPER_PROBLEM = 0x00000040
STATUS_OFFLINE = 0x00000080
STATUS_PRINTING = 0x00000400
STATUS_OUTPUT_BIN_FULL = 0x00000800
STATUS_NOT_AVAILABLE = 0x00001000
STATUS_WAITING = 0x00002000
STATUS_INITIALIZING = 0x00008000
STATUS_TONER_LOW = 0x00020000
STATUS_NO_TONER = 0x00040000
STATUS_USER_INTERVENTION = 0x00100000
STATUS_DOOR_OPEN = 0x00400000
STATUS_POWER_SAVE = 0x01000000

STATUS_LABELS = [
    (STATUS_PAUSED, "已暂停"),
    (STATUS_ERROR, "错误"),
    (STATUS_PAPER_JAM, "卡纸"),
    (STATUS_PAPER_OUT, "缺纸"),
    (STATUS_PAPER_PROBLEM, "纸张问题"),
    (STATUS_OFFLINE, "离线"),
    (STATUS_PRINTING, "正在打印"),
    (STATUS_OUTPUT_BIN_FULL, "出纸口已满"),
    (STATUS_NOT_AVAILABLE, "不可用"),
    (STATUS_WAITING, "等待中"),
    (STATUS_INITIALIZING, "初始化"),
    (STATUS_TONER_LOW, "墨量低"),
    (STATUS_NO_TONER, "墨尽"),
    (STATUS_USER_INTERVENTION, "需人工干预"),
    (STATUS_DOOR_OPEN, "盖未关"),
    (STATUS_POWER_SAVE, "省电模式"),
]

_print_lock = threading.Lock()


# ---------------------------------------------------------------- devmode


# PRINTER_ATTRIBUTE_*（winspool.h）里用得上的几个
ATTR_QUEUED = 0x00000001
ATTR_DIRECT = 0x00000002
ATTR_NETWORK = 0x00000004
ATTR_SHARED = 0x00000008
ATTR_LOCAL = 0x00000040
ATTR_FAX = 0x00001000
ATTR_TS = 0x00002000          # 远程桌面重定向过来的打印机
ATTR_HIDDEN = 0x00004000

# 虚拟 / 软件打印机用的端口。它们不会真正出纸，只是把 GDI 命令导出成文件。
_VIRTUAL_PORT = re.compile(
    r"(PORTPROMPT|XPS_MON|XPSPort|^XPS|SHRFAX|FAX|^FILE:|^nul:|^null:|"
    r"FOXIT|Kingsoft|Virtual|\.pdf|Documents\\|PDFCreator|novaPDF|PDF24|"
    r"Evernote|OneNote| OneNote|tracker|Brain4|SimplePDF|Dopdf)", re.I)

# 能真正出纸的端口形态：本地 USB / 并口 / 串口 / TCP·WSD 网络打印 / UNC 共享队列
_REAL_PORT = re.compile(r"^(USB\d+|LPT\d+:|COM\d+:|IEEE\d+|IP[_0-9]|WSD[-_0-9a-f]|\\{2})", re.I)


def printer_port(printer_name: str) -> str:
    h = win32print.OpenPrinter(printer_name)
    try:
        info = win32print.GetPrinter(h, 2)
    finally:
        win32print.ClosePrinter(h)
    return ((info.get("pPortName") or "").split(",")[0]).strip()


def _printer_info(printer_name: str) -> tuple:
    """取 port 与 Attributes。打不开返回 (?, -1)。"""
    try:
        h = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(h, 2)
        finally:
            win32print.ClosePrinter(h)
        return ((info.get("pPortName") or "").split(",")[0].strip(),
                int(info.get("Attributes", 0) or 0))
    except Exception:
        return ("?", -1)


def is_real_printer(name: str) -> bool:
    """能不能真的打出一页纸。

    主判据是**端口**而不是名字：真实打印机走 USB001 / LPT1: / IP_ / WSD / 网络队列，
    虚拟打印机清一色是 PORTPROMPT: / nul: / FILE: / XPSPort: / *.pdf / 厂商虚拟端口。
    早年那种"按名字里有没有 pdf/adobe 来猜"的办法漏得很厉害——厂商起名花样太多，
    「FD 打印机」「得力打印机」这种名字既猜不出是真是假，也容易误伤。
    """
    if not name:
        return False
    port, attr = _printer_info(name)
    if attr < 0:                 # 连都连不上，肯定不能用
        return False
    if attr & ATTR_FAX:          # 传真是个特殊的"打印机"，别混进来
        return False
    lowport = port.lower()
    if _VIRTUAL_PORT.search(port):
        return False
    if _REAL_PORT.search(port):
        return True
    # 端口认不出来的，再按名字兜一层（比如某些驱动不填端口）
    low = name.lower()
    for bad in ("pdf", "xps", "onenote", "wps", "evernote", "fax", "print to",
                "foxit", "adobe", "虚拟", "打印海报", "一枚打印机"):
        if bad in low:
            return False
    return True


def supported_papers(printer_name: str) -> set:
    """这台打印机驱动真正支持的、且我们认识的纸张，返回 key 集合。

    A4 / Letter 几乎人人都支持，但照片纸（4R/5R code 285/284）只有一部分机器支持。
    硬给它发一个不支持的 PaperSize，驱动多半会悄悄忽略，结果就是打出来的尺寸
    跟预览对不上 —— 你看到某个纸张的可打印比例很反常（比如不同纸张都是同一个数字），
    通常就是这台机器根本没认这张纸，只返回了默认值。

    查不出来的时候返回 None，意思是"别过滤"，宁可多给选项也别把能用的藏起来。
    """
    port, attr = _printer_info(printer_name)
    if attr < 0 or not port or port == "?":
        return None
    try:
        codes = win32print.DeviceCapabilities(printer_name, port, win32con.DC_PAPERS)
    except Exception:
        return None
    if not codes:
        return None
    codes = set(codes)           # 只用来做成员判断，不需要顺序（顺序也不能用，见下）
    return {k for k, p in PAPERS.items() if p["code"] in codes}


def list_printers():
    """本机打印机队列，虚拟打印机已排除。"""
    try:
        names = [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL)]
    except Exception:
        names = []
    names = [n for n in names if n]
    real = [n for n in names if is_real_printer(n)]
    return real or names


def real_printers():
    """列出真实打印机的详细信息，供 UI 选择。"""
    out = []
    for n in ([p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL)]):
        if not n or not is_real_printer(n):
            continue
        port, attr = _printer_info(n)
        out.append({
            "name": n,
            "port": port,
            "net": bool(attr & ATTR_NETWORK),
            "shared": bool(attr & ATTR_SHARED),
            "papers": sorted(supported_papers(n) or set(PAPERS)),
        })
    return out


def default_printer():
    try:
        return win32print.GetDefaultPrinter()
    except Exception:
        names = list_printers()
        return names[0] if names else ""


def _base_devmode(printer_name: str):
    h = win32print.OpenPrinter(printer_name)
    try:
        return win32print.GetPrinter(h, 2)["pDevMode"]
    finally:
        win32print.ClosePrinter(h)


def build_devmode(printer_name: str, paper: str = DEFAULT_PAPER,
                  orientation: int = DMORIENT_PORTRAIT, copies: int = 1):
    """构造指定纸张 / 方向的 DEVMODE。"""
    dm = _base_devmode(printer_name)
    p = PAPERS.get(paper, PAPERS[DEFAULT_PAPER])
    dm.PaperSize = p["code"]
    dm.PaperWidth = p["w"]
    dm.PaperLength = p["l"]
    dm.Orientation = orientation
    dm.Copies = max(1, min(int(copies), 999))
    dm.Color = DMCOLOR_COLOR
    dm.PrintQuality = 360
    dm.Fields = DM_PAPERSIZE | DM_PAPERWIDTH | DM_PAPERLENGTH | DM_ORIENTATION
    return dm


def paper_mm(paper: str, orientation: int = DMORIENT_PORTRAIT):
    p = PAPERS.get(paper, PAPERS[DEFAULT_PAPER])
    w, l = p["w"] / 10.0, p["l"] / 10.0
    return (l, w) if orientation == DMORIENT_LANDSCAPE else (w, l)


def printable_pixels(printer_name: str, paper: str, orientation: int = DMORIENT_PORTRAIT):
    """返回该纸张下的可打印区域像素数（格式：(宽, 高)）。"""
    dm = build_devmode(printer_name, paper, orientation)
    hdc = win32gui.CreateDC("WINSPOOL", printer_name, dm)
    try:
        gd = win32print.GetDeviceCaps
        return gd(hdc, win32con.HORZRES), gd(hdc, win32con.VERTRES)
    finally:
        win32gui.DeleteDC(hdc)


# ---------------------------------------------------------------- 状态


def printer_state(printer_name: str):
    """查询打印机状态与排队作业数。"""
    out = {"name": printer_name, "online": False, "state": "未知", "jobs": 0, "detail": ""}
    h = None
    try:
        h = win32print.OpenPrinter(printer_name)
        info = win32print.GetPrinter(h, 2)  # PRINTER_INFO_2：Status / cJobs / Attributes
        st = int(info.get("Status", 0) or 0)
        out["jobs"] = int(info.get("cJobs", 0) or 0)
        out["online"] = (st & STATUS_OFFLINE) == 0
        labels = []
        for mask, text in STATUS_LABELS:
            if st & mask:
                labels.append(text)
        if not labels:
            labels.append("就绪" if st == 0 else "未知")
        out["state"] = "、".join(labels)
        out["detail"] = str(st)
    except Exception as e:
        out["state"] = "无法查询"
        out["detail"] = str(e)
    finally:
        if h:
            try:
                win32print.ClosePrinter(h)
            except Exception:
                pass
    return out


# ---------------------------------------------------------------- GDI 绘制


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


_gdi = ctypes.windll.gdi32
_gdi.StretchDIBits.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
]
_gdi.StretchDIBits.restype = ctypes.c_int


def _pack_dib(img: Image.Image):
    """把 PIL 图转成内存里的紧凑 DIB（BITMAPINFOHEADER + 像素数据）。"""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    blob = buf.getvalue()[14:]  # 去掉 BITMAPFILEHEADER
    return blob


def _draw_page(hdc, img: Image.Image, pw: int, ph: int):
    """把一个 PIL 图像铺到打印机 DC 上。优先用 ctypes 直调 GDI，失败回落 ImageWin。"""
    try:
        blob = _pack_dib(img)
        buf = ctypes.create_string_buffer(blob)
        ptr = ctypes.cast(buf, ctypes.c_void_p)
        n = _gdi.StretchDIBits(
            hdc, 0, 0, pw, ph,
            0, 0, img.width, img.height,
            ptr, None, DIB_RGB_COLORS, SRCCOPY,
        )
        if n == 0:
            raise RuntimeError("StretchDIBits returned 0")
        return
    except Exception as e:  # noqa: BLE001
        last = e
    # 回落：Pillow 的 ImageWin
    from PIL import ImageWin
    dib = ImageWin.Dib(img.convert("RGB"))
    dib.draw(hdc, (0, 0, pw, ph))


# ---------------------------------------------------------------- 打印


def print_images(printer_name: str, images, paper: str = DEFAULT_PAPER,
                 orientation: int = DMORIENT_PORTRAIT, title: str = "手机打印"):
    """把一组 PIL 图片按页打印。接受生成器：逐张出图、逐张下发，不占一大片内存。"""
    from PIL import Image as _Image  # noqa: F401  (局部引入避免循环依赖)

    with _print_lock:
        dm = build_devmode(printer_name, paper, orientation)
        hdc = win32gui.CreateDC("WINSPOOL", printer_name, dm)
        cdc = win32ui.CreateDCFromHandle(hdc)
        started = False
        try:
            pw = win32print.GetDeviceCaps(hdc, win32con.HORZRES)
            ph = win32print.GetDeviceCaps(hdc, win32con.VERTRES)
            cdc.StartDoc(title)
            started = True
            for idx, im in enumerate(images):
                if im.mode != "RGB":
                    im = im.convert("RGB")
                canvas = Image.new("RGB", (pw, ph), "white")
                # 关键：渲染画布是按质量 DPI（如 200）算的，而打印机 DC 是原生分辨率
                # （这台 L360 是 360dpi）。必须等比放大铺满可打印区，否则内容只有
                # 纸面的 55% 大小——之前"打印出来非常小"就是这个原因。
                scale = min(pw / float(im.width), ph / float(im.height))
                w = max(1, int(round(im.width * scale)))
                h = max(1, int(round(im.height * scale)))
                if (w, h) != (im.width, im.height):
                    im = im.resize((w, h), Image.LANCZOS)
                canvas.paste(im, ((pw - w) // 2, (ph - h) // 2))
                cdc.StartPage()
                _draw_page(cdc.GetHandleOutput(), canvas, pw, ph)
                cdc.EndPage()
            cdc.EndDoc()
            started = False
            return True
        except Exception:
            if started:
                try:
                    cdc.AbortDoc()
                except Exception:
                    pass
            raise
        finally:
            try:
                cdc.DeleteDC()
            except Exception:
                pass


if __name__ == "__main__":
    name = default_printer()
    print("printer:", name)
    for key in ("A4", "A5", "PHOTO_4R", "LETTER"):
        for ori in (DMORIENT_PORTRAIT, DMORIENT_LANDSCAPE):
            pw, ph = printable_pixels(name, key, ori)
            print("  %-9s %s -> %dx%d" % (key, "横" if ori == 2 else "纵", pw, ph))
    print(printer_state(name))
