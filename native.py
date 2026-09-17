# -*- coding: utf-8 -*-
"""原格式直印：让 Office 用自己的打印引擎出纸。

跟「渲染模式」的区别：渲染模式是先把文档转成图片再贴到纸上，版式由我们控制；
直印模式是把文档交给 Word / Excel / PPT 自己打印，版式、分页、页边距、字体
全部由 Office 决定 —— 跟你在电脑上按 Ctrl+P 的结果一模一样，而且是矢量输出，
文字边缘最锐利。

做成独立进程运行，父进程可以按超时杀掉，Office 卡住不会拖垮打印服务。
用法：
    python native.py <源文件绝对路径> <打印机名> <份数> [页码串]
页码串：all（全部）或 1,3,5 / 1-3,7 这类 Word 认识的写法。
返回码：0 成功，其它为失败。
"""
from __future__ import annotations

import os
import sys
import time

WORD_EXT = {"doc", "docx", "rtf", "odt", "dot", "dotx", "docm", "mht", "mhtml", "htm", "html", "xml"}
EXCEL_EXT = {"xls", "xlsx", "xlsm", "xlsb", "csv", "ods"}
PPT_EXT = {"ppt", "pptx", "pptm", "pps", "ppsx", "odp"}

# WdPrintOutRange
WD_PRINT_ALL = 0
# WdPrintOutPages：这才是 Word 官方的「只打奇数页 / 只打偶数页」开关。
# 早先用 Pages="1,3" 那种页码串，实测会让 EMF 作业卡在队列里打不出来，别再用。
WD_PRINT_ALL_PAGES = 0
WD_PRINT_ODD_ONLY = 1
WD_PRINT_EVEN_ONLY = 2
PAGE_TYPE = {"all": WD_PRINT_ALL_PAGES, "odd": WD_PRINT_ODD_ONLY, "even": WD_PRINT_EVEN_ONLY}


def _wait_spool(printer: str, baseline: int, timeout: float = 30.0):
    """等打印作业真正落进队列，避免父进程以为打完了其实还没送出去。"""
    try:
        import win32print
    except Exception:
        return
    end = time.time() + timeout
    while time.time() < end:
        try:
            h = win32print.OpenPrinter(printer)
            try:
                jobs = win32print.EnumJobs(h, 0, -1, 1)
                pending = sum(1 for j in jobs if j.get("Status", 0) & 0x00000100 == 0)
                if jobs and (pending > 0 or len(jobs) > baseline):
                    return
            finally:
                win32print.ClosePrinter(h)
        except Exception:
            return
        time.sleep(0.5)


def print_word(src: str, printer: str, copies: int, phase: str = "all"):
    import win32com.client as win32
    # Word 改 ActivePrinter 会连带把「系统默认打印机」改掉，而它记住的旧值本身
    # 也可能已经是脏的 —— 所以这里以系统默认打印机为准，打完兜底还原。
    try:
        import win32print
        sys_default = win32print.GetDefaultPrinter()
    except Exception:
        win32print = None
        sys_default = None
    word = win32.DispatchEx("Word.Application")
    old_printer = None
    try:
        word.Visible = False
        word.DisplayAlerts = 0
        try:
            old_printer = word.ActivePrinter
        except Exception:
            pass
        # 关键：把 Word 的活动打印机切到目标机，否则打到系统默认机上
        if printer and printer != old_printer:
            try:
                word.ActivePrinter = printer
            except Exception as e:
                print("warn: 切换打印机失败 %s（将用 Word 当前默认机）" % e, file=sys.stderr)
        doc = word.Documents.Open(os.path.abspath(src), ReadOnly=True, AddToRecentFiles=False)
        try:
            doc.PrintOut(Background=False, Range=WD_PRINT_ALL,
                         Copies=copies, PageType=PAGE_TYPE.get(phase, WD_PRINT_ALL_PAGES))
        finally:
            doc.Close(False)
    finally:
        try:
            word.Quit()
        except Exception:
            pass
        if win32print and sys_default:
            try:
                if win32print.GetDefaultPrinter() != sys_default:
                    win32print.SetDefaultPrinter(sys_default)
            except Exception:
                pass


def print_excel(src: str, printer: str, copies: int, phase: str = "all"):
    """Excel 按工作表自己的打印区域/缩放打印，不在代码里改任何页面设置。"""
    import win32com.client as win32
    excel = win32.DispatchEx("Excel.Application")
    try:
        excel.Visible = False
        excel.DisplayAlerts = False
        wb = excel.Workbooks.Open(os.path.abspath(src), ReadOnly=True, UpdateLinks=0)
        try:
            kwargs = {"Copies": copies, "Collate": True}
            if printer:
                kwargs["ActivePrinter"] = printer
            wb.PrintOut(**kwargs)
        finally:
            wb.Close(SaveChanges=False)
    finally:
        try:
            excel.Quit()
        except Exception:
            pass


def print_ppt(src: str, printer: str, copies: int, phase: str = "all"):
    import win32com.client as win32
    ppt = win32.DispatchEx("PowerPoint.Application")
    try:
        pres = ppt.Presentations.Open(os.path.abspath(src), ReadOnly=True, WithWindow=False)
        try:
            if printer:
                try:
                    pres.PrintOptions.ActivePrinter = printer
                except Exception as e:
                    print("warn: PPT 切换打印机失败 %s（将用默认机）" % e, file=sys.stderr)
            pres.PrintOut(Copies=copies)
        finally:
            pres.Close()
    finally:
        try:
            ppt.Quit()
        except Exception:
            pass


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: native.py <src> <printer> <copies> [all|odd|even]", file=sys.stderr)
        return 2
    src = sys.argv[1]
    printer = sys.argv[2]
    try:
        copies = max(1, min(int(sys.argv[3]), 99))
    except ValueError:
        copies = 1
    phase = (sys.argv[4] if len(sys.argv) > 4 else "all").lower()
    if phase not in PAGE_TYPE:
        phase = "all"

    ext = os.path.splitext(src)[1].lower().lstrip(".")
    if ext in WORD_EXT:
        print_word(src, printer, copies, phase)
    elif ext in EXCEL_EXT:
        # Excel / PPT 没有「只打奇数页」的接口，手动双面时不能走直印
        if phase != "all":
            print("Excel 不支持手动双面直印，请改用「网页排版」方式", file=sys.stderr)
            return 4
        print_excel(src, printer, copies, phase)
    elif ext in PPT_EXT:
        if phase != "all":
            print("PPT 不支持手动双面直印，请改用「网页排版」方式", file=sys.stderr)
            return 4
        print_ppt(src, printer, copies, phase)
    else:
        print("unsupported ext: %s" % ext, file=sys.stderr)
        return 3
    _wait_spool(printer, 0)
    return 0


if __name__ == "__main__":
    import pythoncom

    pythoncom.CoInitialize()
    try:
        sys.exit(main())
    finally:
        pythoncom.CoUninitialize()
