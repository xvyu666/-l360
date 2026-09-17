# -*- coding: utf-8 -*-
"""后台把 Office 文档转成 PDF。

做成独立进程运行：Word/Excel/PPT 偶发卡死时，父进程可以按超时直接杀掉，
不会把打印服务整个拖住。
用法：python office.py <源文件绝对路径> <输出PDF绝对路径>
"""
from __future__ import annotations

import os
import sys

WORD_EXT = {"doc", "docx", "rtf", "odt", "dot", "dotx", "docm", "mht", "mhtml", "htm", "html", "xml"}
EXCEL_EXT = {"xls", "xlsx", "xlsm", "xlsb", "csv", "ods", "xlsm"}
PPT_EXT = {"ppt", "pptx", "pptm", "pps", "ppsx", "odp"}

WD_FORMAT_PDF = 17
XL_TYPE_PDF = 0
PP_SAVE_AS_PDF = 32


def _cleanup(app, quit_method="Quit"):
    try:
        getattr(app, quit_method)()
    except Exception:
        pass


def convert_word(src: str, dst: str):
    import win32com.client as win32
    word = win32.DispatchEx("Word.Application")
    try:
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(os.path.abspath(src), ReadOnly=True, AddToRecentFiles=False)
        try:
            doc.SaveAs2(os.path.abspath(dst), FileFormat=WD_FORMAT_PDF)
        except Exception:
            doc.ExportAsFixedFormat(os.path.abspath(dst), ExportFormat=WD_FORMAT_PDF)
        doc.Close(False)
    finally:
        _cleanup(word)


def convert_excel(src: str, dst: str):
    import win32com.client as win32
    excel = win32.DispatchEx("Excel.Application")
    try:
        excel.Visible = False
        excel.DisplayAlerts = False
        wb = excel.Workbooks.Open(os.path.abspath(src), ReadOnly=True, UpdateLinks=0)
        try:
            for ws in wb.Worksheets:
                try:
                    ws.PageSetup.Zoom = False
                    ws.PageSetup.FitToPagesWide = 1
                    ws.PageSetup.FitToPagesTall = False
                except Exception:
                    pass
            wb.ExportAsFixedFormat(XL_TYPE_PDF, os.path.abspath(dst))
        except Exception:
            wb.SaveAs(os.path.abspath(dst), 57)
        wb.Close(SaveChanges=False)
    finally:
        _cleanup(excel)


def convert_ppt(src: str, dst: str):
    import win32com.client as win32
    ppt = win32.DispatchEx("PowerPoint.Application")
    try:
        pres = ppt.Presentations.Open(os.path.abspath(src), ReadOnly=True, WithWindow=False)
        try:
            pres.SaveAs(os.path.abspath(dst), PP_SAVE_AS_PDF)
        except Exception:
            pres.Export(os.path.abspath(dst), "PDF")
        pres.Close()
    finally:
        _cleanup(ppt)


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: office.py <src> <dst>", file=sys.stderr)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    ext = os.path.splitext(src)[1].lower().lstrip(".")
    if ext in WORD_EXT:
        convert_word(src, dst)
    elif ext in EXCEL_EXT:
        convert_excel(src, dst)
    elif ext in PPT_EXT:
        convert_ppt(src, dst)
    else:
        print("unsupported ext: %s" % ext, file=sys.stderr)
        return 3
    return 0 if os.path.exists(dst) else 4


if __name__ == "__main__":
    import pythoncom

    pythoncom.CoInitialize()
    try:
        sys.exit(main())
    finally:
        pythoncom.CoUninitialize()
