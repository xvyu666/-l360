# -*- coding: utf-8 -*-
"""打印链路自检：不碰到真实打印机，只在内存里把整条路走一遍。

验证内容：
  1. 各种纸张 + 方向能否正确建立打印 DC
  2. DIB 打包是否正常
  3. 能不能把一张图真正画进 DC（用内存 DC 代替打印机，避免浪费纸）
"""
from __future__ import annotations

import os
import tempfile

from PIL import Image, ImageDraw

import printer_core as PC
import renderer as RD
import server


def log(*a):
    print(*a, flush=True)


def main():
    printer = PC.default_printer()
    log("printer:", printer)
    ok = True

    # 1. devmode / DC
    import win32gui
    import win32print
    import win32con
    for key in PC.PAPERS:
        for ori in (PC.DMORIENT_PORTRAIT, PC.DMORIENT_LANDSCAPE):
            try:
                pw, ph = PC.printable_pixels(printer, key, ori)
                log("  [OK] %-9s %s -> %4dx%-4d" % (key, "横" if ori == 2 else "纵", pw, ph))
            except Exception as e:
                ok = False
                log("  [FAIL] %-9s %s -> %s" % (key, ori, e))
    # 2. DIB 打包
    try:
        probe = Image.new("RGB", (80, 50), (200, 30, 30))
        blob = PC._pack_dib(probe)
        ok_blob = len(blob) > probe.width * probe.height * 3
        log("  [OK] pack_dib %d bytes (expect > %d)" % (len(blob), probe.width * probe.height * 3))
        if not ok_blob:
            ok = False
    except Exception as e:
        ok = False
        log("  [FAIL] pack_dib:", e)

    # 3. 往内存 DC 上画（等价于最终 print_images 的绘制动作）
    try:
        hdc = win32gui.CreateCompatibleDC(None)
        bmp = win32gui.CreateCompatibleBitmap(hdc, 400, 300)
        win32gui.SelectObject(hdc, bmp)
        img = Image.new("RGB", (200, 150), "white")
        ImageDraw.Draw(img).ellipse([20, 20, 180, 130], outline=(10, 10, 10), width=4)
        PC._draw_page(hdc, img, 400, 300)
        win32gui.DeleteObject(bmp)
        win32gui.DeleteDC(hdc)
        log("  [OK] draw into memory DC")
    except Exception as e:
        ok = False
        log("  [FAIL] draw into memory DC:", e)

    # 4. PyCDC 包装（真正打印时用的对象）
    try:
        import win32ui
        dm = PC.build_devmode(printer, "A4", PC.DMORIENT_PORTRAIT)
        raw = win32gui.CreateDC("WINSPOOL", printer, dm)
        cdc = win32ui.CreateDCFromHandle(raw)
        doc = cdc.GetDocumentationProperties() if hasattr(cdc, "GetDocumentationProperties") else None
        cdc.DeleteDC()
        log("  [OK] win32ui CDC from handle")
    except Exception as e:
        ok = False
        log("  [FAIL] win32ui CDC:", e)

    # 5. 完整渲染：造一个假任务，走 server 的 Canvas 计算 + compose
    try:
        src = RD.make_text_source("自检文本 Hello 打印\n第二行测试自动换行\n" * 6, 12)
        for paper in ("A4", "A5", "PHOTO_4R"):
            for layout in ("fit", "fill", "natural"):
                canvas_px, canvas_mm = server.canvas_for(printer, paper, PC.DMORIENT_PORTRAIT, 200)
                img = src.render(0, 300)
                out = RD.compose_page(img, src.page_size_mm(0), canvas_px, canvas_mm, layout, False)
                if out.size != canvas_px:
                    ok = False
                    log("  [FAIL] %s/%s size %s != %s" % (paper, layout, out.size, canvas_px))
        log("  [OK] canvas + compose across papers/layouts")
        src.close()
    except Exception as e:
        ok = False
        log("  [FAIL] compose pipeline:", e)

    # 6. 作业与预览链路（不落盘打印机）
    try:
        server.ensure_dirs()
        server.load_config()
        job = server.new_job("自检.txt", "text", "", 1, text="自检 **测试** 内容", font_pt=12)
        j = server.public(job)
        log("  [OK] job created:", j["id"], j["pages"], "页")
        server.drop_job(job["id"])
        log("  [OK] job dropped")
    except Exception as e:
        ok = False
        log("  [FAIL] job lifecycle:", e)

    log("\n=== 自检%s ===" % ("通过" if ok else "发现失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
