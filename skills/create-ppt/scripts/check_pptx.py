#!/usr/bin/env python3
# /// script
# dependencies = ["python-pptx>=1.0"]
# ///
"""成品自检：残留占位与文本溢出。渲染后跑一遍，有问题就改大纲文案再重渲。

用法：
    uv run scripts/check_pptx.py --outline outline.json 输出.pptx

溢出判据取自模板索引里每个槽位的 cap（几何容量与模板原文案长度的较大值），
不重新猜几何——单靠几何会把 LOGO、目录这类模板自带的设计文字误报成溢出。
不依赖任何渲染器，任何环境都能跑。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pptx import Presentation

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pptx_util import iter_text_shapes, load_index  # noqa: E402

PLACEHOLDER_HINTS = ("单击此处", "单击添加", "此处添加", "本模板内", "请添加", "请自行替换", "XXXX", "{")
TOLERANCE = 1.15  # 允许超 15%：cap 是估算值，卡太死会报一堆假警


def check(outline: dict, path: Path) -> int:
    index = {s["i"]: s for s in load_index(outline["template"])["slides"]}
    prs = Presentation(str(path))
    issues: list[str] = []

    for n, (page, slide) in enumerate(zip(outline["pages"], prs.slides), 1):
        caps = {
            slot["sid"]: slot["cap"]
            for slot in index[page["src"]]["slots"]
            if "cap" in slot
        }
        for shape in iter_text_shapes(slide.shapes):
            text = shape.text_frame.text.strip()
            if not text:
                continue
            if any(hint in text for hint in PLACEHOLDER_HINTS):
                issues.append(f"  [残留占位] 第 {n} 页：{text[:24]}")
            cap = caps.get(shape.shape_id)
            if cap and len(text) > cap * TOLERANCE:
                issues.append(f"  [文本溢出] 第 {n} 页：{len(text)} 字 / 上限 {cap} 字 — {text[:24]}")

    print(f"{path.name}：{len(prs.slides)} 页")
    print("\n".join(issues) if issues else "  通过：无残留占位、无文本溢出")
    return len(issues)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outline", required=True)
    parser.add_argument("pptx")
    args = parser.parse_args()

    outline = json.loads(Path(args.outline).read_text(encoding="utf-8"))
    sys.exit(1 if check(outline, Path(args.pptx)) else 0)


if __name__ == "__main__":
    main()
