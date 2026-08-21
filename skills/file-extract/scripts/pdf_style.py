#!/usr/bin/env python3
# /// script
# dependencies = []
# ///
"""
PDF 排版样式画像。pypdf 由 readers.load_pypdf 提供（环境已装的优先，否则用 vendor wheel）。

PDF 里没有「段落样式」这种东西，能拿到的只有每段文字的字体、字号与基线位置。
字体、字号是直接读出来的；行距是**测**出来的——同页相邻基线差的众数；页边距是
按文本块外框**推算**的，会被页眉页脚干扰，所以标成 approx，不与精确值同等对待。

对外接口：
  profile(data: bytes, max_pages: int = 30) -> dict   与 docx_style.profile 同构
"""
import collections
import io
import re

import readers

# 内嵌字体的子集前缀形如 FAAAAI+SimSun，比对前要剥掉
SUBSET_PREFIX_RE = re.compile(r"^[A-Z]{6}\+")

PT_TO_MM = 25.4 / 72


def _font_name(font_dict) -> tuple[str, bool]:
    """返回 (字体名, 是否加粗)。PDF 把粗体写成 SimSun,Bold 或 SimSun-Bold 这类后缀。"""
    raw = str((font_dict or {}).get("/BaseFont", "")).lstrip("/")
    name = SUBSET_PREFIX_RE.sub("", raw)
    bold = bool(re.search(r"[,\-](Bold|Black|Heavy)", name, re.IGNORECASE))
    name = re.split(r"[,\-](?:Bold|Black|Heavy|Italic|Oblique|Regular|MT|PSMT)", name)[0]
    return name or "未知", bold


def profile(data: bytes, max_pages: int = 30) -> dict:
    reader = readers.load_pypdf()(io.BytesIO(data))
    pages = reader.pages[:max_pages]

    runs: list[dict] = []
    lines: list[dict] = []
    box = {"left": None, "right": None, "top": None, "bottom": None}
    page_size = None
    deltas: collections.Counter = collections.Counter()

    for page in pages:
        if page_size is None:
            page_size = [round(float(page.mediabox.width) * PT_TO_MM, 1),
                         round(float(page.mediabox.height) * PT_TO_MM, 1)]
        page_lines: dict[float, dict] = {}

        def visitor(text, cm, tm, font_dict, font_size):
            if not text.strip():
                return
            font, bold = _font_name(font_dict)
            size = round(float(font_size), 1)
            # 基线的真实位置要把文本矩阵与当前变换矩阵复合起来算
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            chars = len(text.strip())

            runs.append({"font": font, "size_pt": size, "bold": bold, "chars": chars})
            key = round(y, 1)
            line = page_lines.setdefault(key, {"text": "", "size_pt": size, "font": font,
                                               "bold": bold, "chars": 0, "y": key, "x": x})
            line["text"] += text
            line["chars"] += chars
            box["left"] = x if box["left"] is None else min(box["left"], x)
            box["right"] = x if box["right"] is None else max(box["right"], x)
            box["top"] = y if box["top"] is None else max(box["top"], y)
            box["bottom"] = y if box["bottom"] is None else min(box["bottom"], y)

        page.extract_text(visitor_text=visitor)

        ordered = sorted(page_lines, reverse=True)
        for index in range(len(ordered) - 1):
            deltas[round(ordered[index] - ordered[index + 1], 1)] += 1
        lines.extend(page_lines[key] for key in ordered)

    height_pt = (page_size[1] / PT_TO_MM) if page_size else 0
    margins = {}
    if box["left"] is not None and height_pt:
        # 只推左边距与上边距：它们由文字的起始位置决定，稳定。右边距取决于每行排到哪里、
        # 下边距取决于末页排到哪里，用文本块外框推出来必然偏大，是稳定的假阳性来源。
        margins = {
            "top": round((height_pt - box["top"]) * PT_TO_MM, 1),
            "left": round(box["left"] * PT_TO_MM, 1)
        }

    measured = deltas.most_common(1)[0][0] if deltas else None
    paragraphs = [{"text": line["text"].strip(), "chars": line["chars"],
                   "size_pt": line["size_pt"], "font": line["font"], "bold": line["bold"],
                   "align": None, "first_line_indent": None,
                   "line_spacing": {"mode": "measured", "pt": measured} if measured else None}
                  for line in lines if line["text"].strip()]

    page = {"size_mm": page_size} if page_size else {}
    if margins:
        page["margins_mm"] = margins
        page["margins_approx"] = True
        page["margins_unavailable"] = ["right", "bottom"]

    return {"source": "pdf", "page": page, "runs": runs, "paragraphs": paragraphs,
            "measured_line_spacing_pt": measured, "pages_read": len(pages)}
