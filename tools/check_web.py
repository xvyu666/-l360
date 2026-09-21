"""静态自检前端：有没有点了没反应的按钮。

为什么需要它：手机浏览器不会把 JS 报错弹出来，
onclick 写错一个函数名，表现就是「按下去毫无反应」，肉眼极难发现。
所以这里在发布前先把两件事查一遍：

    1. HTML 里 onclick/oninput/onchange 调用的函数，是不是真定义了
    2. JS 里 $('#xxx') 引用到的 DOM id，页面上是不是真有

用法：
    python tools/check_web.py
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")

# 页面自己用字符串拼出来调用的函数（并非真的漏定义），以及习惯性忽略的内置
ASSUMED = {"toggleOpts", "setMargin"}


def read(p):
    return open(p, encoding="utf-8", errors="replace").read()


def main():
    html = read(os.path.join(WEB, "index.html"))
    js_files = [f for f in os.listdir(WEB) if f.endswith(".js")]
    js = "\n".join(read(os.path.join(WEB, f)) for f in sorted(js_files))
    inline = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    allsrc = js + "\n" + inline

    # 1) 定义与调用对不上
    called = set(re.findall(r'on(?:click|input|change|focus)="([A-Za-z_$][\w$]*)\s*\(', html))
    defined = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", allsrc))
    defined |= set(re.findall(r"([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\()", allsrc))
    missing = sorted(n for n in called if n not in defined and n not in ASSUMED)

    # 2) JS 引用了但页面上没有的 id
    ids = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r"""\$[A-Za-z_$]*\s*\(\s*['"]#([\w-]+)""", allsrc))
    missing_ids = sorted(used - ids)

    # 3) 排版「下一步」面板里的 .seg 不能带 data-key，
    #    否则会被主页那段统一代理抢去改 S.opts，而不是 ED.opts
    seg_block = html.split('id="edPoMask"')[-1].split("<!-- 打印预览")[0]
    bad_seg = re.findall(r'<div class="seg[^"]*"\s+data-key="[^"]*"', seg_block)

    print("=== onclick 里调用的函数：%d 个 ===" % len(called))
    print("   找不到定义：%s" % (", ".join(missing) or "无"))
    print()
    print("=== JS 引用的 DOM id：%d 个 ===" % len(used))
    print("   页面上没有：%s" % (", ".join(missing_ids) or "无"))
    print()
    print("=== edPoMask 里误带 data-key 的 seg（应为 0）：%d 个 ===" % len(bad_seg))

    ok = not (missing or missing_ids or bad_seg)
    print()
    print("结论：%s" % ("全部通过" if ok else "有问题，见上"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
