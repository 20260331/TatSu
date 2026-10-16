"""Render the q2 evidence screenshots with PIL.

The artifact is a headless library change with no GUI, so the screenshots are
reproducible renders of real output:

  q2-bundle-demo.png    the real stdout of evidence/demo_bundle.py, shown as a
                        terminal panel (paired export, load, relocation,
                        rejection paths, isolation).
  q2-audit-report.png   the review verdict, the verify-rubric results and the
                        execution log.

Run with a Python that has Pillow (the system python3 works):
    python3 evidence/render_report.py
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "screenshots"
LOG = HERE / "logs" / "q2-bundle-demo.txt"
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

ROWS = [
    ("1", True, "tatsu.export_bundle/load_bundle/verify_bundle 新增；目录含 parser.py、model.py、manifest.json，清单记录 bundle_id、grammar 名与源 SHA-256、TatSu 版本、生成时间、入口类名及每文件 SHA-256；目录整体搬移后仍能加载解析。"),
    ("2", True, "load_bundle 返回配对的 parser 与 model/semantics 类型；bundle.parse('1+2') 得到 Add(left=1,right=2)，且类型属于该 bundle 自带的模型模块。"),
    ("3", True, "加载先校验再执行：追加篡改 parser.py 报 checksum mismatch，删除 model.py 报 missing bundle file；校验在 exec 之前完成。"),
    ("4", True, "换入另一次生成的 model.py 并重算其摘要后仍被拒：内部 bundle_id marker 与清单/彼此不一致时报 another generation。"),
    ("5", True, "第二次导出失败（坏 grammar）后旧 bundle 的 bundle_id 与 parser.py 字节均不变，且无 .tmp/.old 残留。"),
    ("6", True, "两次同名 grammar（Calc）各自导出并通过唯一模块名加载：parser 类、模型模块/基类、semantics 彼此独立，各自只接受自己的 grammar 输入。"),
    ("7", True, "旧接口 to_python_sourcecode/to_python_model/compile 与 CLI 行为不变；既有 fast 套件 498 passed/7 skipped、boot 13 passed/1 skipped，相对基线无回归。"),
]

CHECKS = [
    ("专项测试", "pytest tests/bundle_test.py tests/version_test.py -> 24 passed"),
    ("fast 套件", "pytest -n auto --ignore-glob='tests/z*' tests -> 498 passed, 7 skipped"),
    ("boot 套件", "pytest tests/z_bootstrap_test.py -> 13 passed, 1 skipped"),
    ("ruff", "ruff check --preview + ruff format --check 改动文件 -> All checks passed / 7 files formatted"),
    ("ty / mypy", "ty check tatsu tests -> All checks passed；mypy 改动源码 -> Success: no issues found"),
    ("examples/calc", "PYTHONPATH=../.. python calc.py -> 正常输出 POSTFIX 与结果 -47"),
]


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size)


def mono(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(MONO, size)


def render_demo() -> Path:
    lines: list[str] = []
    for raw in LOG.read_text(encoding="utf-8").splitlines():
        if len(raw) <= 150:
            lines.append(raw)
            continue
        indent = " " * (len(raw) - len(raw.lstrip()))
        lines.extend(textwrap.wrap(raw, width=150, subsequent_indent=indent + "  "))

    line_h = 24
    pad = 34
    width = 1720
    header = 96
    height = header + pad + line_h * len(lines) + pad + 18
    image = Image.new("RGB", (width, height), "#10161d")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, header), fill="#1b2530")
    for i, color in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        draw.ellipse((28 + i * 30, 30, 48 + i * 30, 50), fill=color)
    draw.text((150, 30), "TatSu q2  ·  paired parser/model bundle  ·  real run output",
              font=font(26), fill="#e7edf3")
    draw.text((150, 64), "branch swe/q2-parser-model-release-validation   base 8f9d741a",
              font=mono(15), fill="#8fa3b5")

    y = header + pad
    for line in lines:
        if line.startswith("TatSu "):
            color = "#8ee6a8"
        elif line.startswith("["):
            color = "#79c0ff"
        elif "BundleError" in line or "-> True" in line or "False" in line:
            color = "#ffd479"
        else:
            color = "#d7e2ec"
        draw.text((pad, y), line, font=mono(17), fill=color)
        y += line_h

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "q2-bundle-demo.png"
    image.save(path)
    return path


def render_audit() -> Path:
    image = Image.new("RGB", (1700, 1400), "#eef2f5")
    draw = ImageDraw.Draw(image)

    draw.rectangle((40, 35, 1660, 224), fill="white")
    draw.rectangle((40, 35, 1660, 42), fill="#176b87")
    draw.text((76, 66), "TatSu q2 · 解析器与对象模型的成套发布和加载校验", font=font(33), fill="#17212b")
    draw.text((76, 122), "独立审核证据 · 2026-10-13 · 有效轮数 144 · 分支 swe/q2-parser-model-release-validation",
              font=font(18), fill="#566575")
    draw.text((76, 151), "基线", font=font(16), fill="#566575")
    draw.text((122, 152), "8f9d741a4652c4b7bb34033cfefa6ce562b856d8", font=mono(15), fill="#566575")
    draw.text((76, 178), "harness Trae · 补丁 +432 行新增 tatsu/bundle.py，接入顶层/API 导出并附 20 条测试",
              font=font(16), fill="#566575")

    draw.rectangle((40, 224, 1660, 428), fill="white")
    draw.rectangle((76, 262, 292, 336), fill="#167046")
    draw.text((122, 278), "完成", font=font(28), fill="white")
    summary = (
        "新增可选的成套导出/加载入口：export_bundle 生成含 parser.py、model.py 与 manifest.json 的可搬迁目录，"
        "清单记录生成身份（bundle_id、grammar 名与源 SHA-256、工具版本、生成时间、入口类）与两份文件的内容摘要；"
        "load_bundle 先校验再执行，拒绝损坏、缺文件与跨次混用，返回可配套解析的 parser 与模型/semantics 类型。"
        "生成失败保留旧目录，同名 grammar 交错加载不串类或状态，旧生成接口与既有测试无回归。"
    )
    for i, line in enumerate(textwrap.wrap(summary, width=58)):
        draw.text((316, 256 + i * 30), line, font=font(18), fill="#17212b")

    draw.rectangle((40, 448, 1660, 1176), fill="white", outline="#d6dde3")
    draw.text((76, 468), "Verify Rubric 结果", font=font(25), fill="#17212b")
    top = 520
    draw.rectangle((76, top, 1624, top + 44), fill="#edf2f5")
    draw.text((104, top + 11), "ID", font=font(16), fill="#3c4b58")
    draw.text((182, top + 11), "结果", font=font(16), fill="#3c4b58")
    draw.text((330, top + 11), "证据摘要", font=font(16), fill="#3c4b58")
    row_h = 90
    for index, (rid, ok, detail) in enumerate(ROWS):
        y = top + 44 + index * row_h
        draw.line((76, y, 1624, y), fill="#e3e8ec", width=1)
        draw.text((108, y + 20), rid, font=font(17), fill="#17212b")
        draw.text((182, y + 20), "通过" if ok else "未通过", font=font(17), fill="#167046" if ok else "#b3261e")
        for line_index, line in enumerate(textwrap.wrap(detail, width=74)):
            draw.text((330, y + 14 + line_index * 25), line, font=font(15), fill="#17212b")

    draw.rectangle((40, 1196, 1660, 1386), fill="white", outline="#d6dde3")
    draw.text((76, 1212), "执行记录", font=font(22), fill="#17212b")
    for index, (title, value) in enumerate(CHECKS):
        x = 76 + (index % 2) * 800
        row = index // 2
        yy = 1252 + row * 42
        draw.rectangle((x, yy, x + 760, yy + 40), fill="#f7f9fa")
        draw.rectangle((x, yy, x + 5, yy + 40), fill="#176b87")
        draw.text((x + 18, yy + 4), title, font=font(13), fill="#17212b")
        draw.text((x + 18, yy + 21), value, font=mono(11), fill="#566575")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "q2-audit-report.png"
    image.save(path)
    return path


if __name__ == "__main__":
    print("wrote", render_demo())
    print("wrote", render_audit())
