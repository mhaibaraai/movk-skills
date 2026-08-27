#!/usr/bin/env python3
# /// script
# dependencies = []
# ///
"""
排版样式画像与比对。零第三方依赖，直接 python3 调用。

合同的「格式审查」在纯文本上做不了——字体、字号、行距在抽取时就被丢掉了。这个脚本
把一份 docx / pdf 归纳成一份「样式画像」，两份画像之间的差异由脚本算出来，措辞与
分级交给模型。

**只比两边都拿得到的项**，其余进 not_comparable 并写明原因（PDF 里没有段落缩进这种
东西，硬比就是编造）。

CLI:
  python3 scripts/styles.py --path 合同.pdf
  python3 scripts/styles.py --path 合同.pdf --template 标准模板.docx

输出 JSON: {contract: {...}, template: {...}, diffs: [...], not_comparable: [...]}
退出码: 0 正常；2 参数错误；1 文件读不了。
"""
import argparse
import collections
import json
import re
import sys
from pathlib import Path

import docx_style
import pdf_style

# PDF 内嵌的是西文字体名，docx 里写的是中文名，不映射就会把同一种字体报成差异
FONT_ALIASES = {
    "simsun": "宋体", "nsimsun": "新宋体", "simhei": "黑体",
    "kaiti": "楷体", "kaiti_gb2312": "楷体", "stkaiti": "楷体",
    "fangsong": "仿宋", "fangsong_gb2312": "仿宋", "stfangsong": "仿宋",
    "microsoft yahei": "微软雅黑", "msyh": "微软雅黑",
    "times new roman": "Times New Roman", "timesnewroman": "Times New Roman",
    "arial": "Arial", "calibri": "Calibri"
}

CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 只认顶层条款体系，按下面的优先级取第一个用得起来的。阿拉伯序号故意不收——
# 合同正文里的 1. 2. 是条款内部的层级编号，会分节重启，对它查断号只会产出噪音。
ARTICLE_PATTERNS = (
    ("第X条", re.compile(r"^第([一二三四五六七八九十百]+)条")),
    ("中文序号", re.compile(r"^([一二三四五六七八九十百]+)[、.．]"))
)
MIN_ARTICLE_COUNT = 3

# 页边距在 PDF 上是按文本块外框推算的，给足容差才不会报出一堆假差异
MARGIN_TOLERANCE_MM = 5.0
LINE_SPACING_TOLERANCE_PT = 1.5
# Word 的单倍行距约为字号的 1.2 倍，倍数行距换算成磅值时用它
SINGLE_LINE_FACTOR = 1.2


def normalize_font(name: str | None) -> str | None:
    if not name:
        return None
    return FONT_ALIASES.get(name.strip().lower(), name.strip())


def cn_to_int(text: str) -> int | None:
    """「二十一」→ 21。只处理合同条款序号会用到的百以内写法。"""
    if not text or any(ch not in CN_DIGITS for ch in text):
        return None
    if "十" not in text:
        return CN_DIGITS[text[0]] if len(text) == 1 else None
    tens, _, ones = text.partition("十")
    return (CN_DIGITS[tens] if tens else 1) * 10 + (CN_DIGITS[ones] if ones else 0)


def _share(items: list[dict], key: str) -> list[dict]:
    total = sum(item["chars"] for item in items) or 1
    counter: collections.Counter = collections.Counter()
    for item in items:
        if item.get(key) is not None:
            counter[item[key]] += item["chars"]
    return [{key: value, "share": round(chars / total, 3)}
            for value, chars in counter.most_common(8)]


def _articles(paragraphs: list[dict]) -> dict:
    """条款序号的连续性。断号是最常见也最容易漏看的排版问题。"""
    for name, pattern in ARTICLE_PATTERNS:
        found: list[tuple[int, str]] = []
        for paragraph in paragraphs:
            match = pattern.match(paragraph["text"].strip())
            value = cn_to_int(match.group(1)) if match else None
            if value:
                found.append((value, paragraph["text"].strip()[:16]))
        if len(found) < MIN_ARTICLE_COUNT:
            continue
        # 带上后一条的原文，报告里才定位得到。附件重新起编号也会命中，交给模型判断
        gaps = [f"{found[i][0]} 之后直接是「{found[i + 1][1]}」"
                for i in range(len(found) - 1) if found[i + 1][0] != found[i][0] + 1]
        numbers = [value for value, _ in found]
        return {"scheme": name, "count": len(numbers), "max": max(numbers), "gaps": gaps}

    return {"scheme": None, "count": 0, "max": 0, "gaps": []}


def summarize(raw: dict) -> dict:
    """把逐段/逐 run 的原始格式归纳成画像。"""
    runs, paragraphs = raw["runs"], raw["paragraphs"]
    for item in runs + paragraphs:
        item["font"] = normalize_font(item.get("font"))

    sizes = _share(runs, "size_pt")
    body_size = sizes[0]["size_pt"] if sizes else None
    body_runs = [run for run in runs if run.get("size_pt") == body_size]
    body_fonts = _share(body_runs, "font")

    body_paragraphs = [p for p in paragraphs if p.get("size_pt") == body_size]
    spacings = [json.dumps(p["line_spacing"], sort_keys=True)
                for p in body_paragraphs if p.get("line_spacing")]
    indents = [json.dumps(p["first_line_indent"], sort_keys=True)
               for p in body_paragraphs if p.get("first_line_indent")]

    headings = []
    seen = set()
    for paragraph in paragraphs:
        size = paragraph.get("size_pt")
        is_heading = (size and body_size and size > body_size) or (
            paragraph.get("bold") and paragraph.get("align") == "center")
        text = paragraph["text"].strip()[:40]
        if not is_heading or text in seen:
            continue
        seen.add(text)
        headings.append({"text": text, "size_pt": size, "font": paragraph.get("font"),
                         "bold": bool(paragraph.get("bold")), "align": paragraph.get("align")})

    body = {
        "font": body_fonts[0]["font"] if body_fonts else None,
        "size_pt": body_size,
        "share": sizes[0]["share"] if sizes else 0,
        "line_spacing": json.loads(collections.Counter(spacings).most_common(1)[0][0]) if spacings else None,
        "first_line_indent": json.loads(collections.Counter(indents).most_common(1)[0][0]) if indents else None
    }

    unavailable = [key for key in ("line_spacing", "first_line_indent") if body[key] is None]
    if raw["source"] == "pdf":
        unavailable = [key for key in unavailable if key != "line_spacing"]
        unavailable.append("align")

    return {
        "source": raw["source"],
        "page": raw["page"],
        "body": body,
        "headings": headings[:15],
        "fonts": _share(runs, "font"),
        "sizes": sizes,
        "articles": _articles(paragraphs),
        "unavailable": sorted(set(unavailable))
    }


def profile_file(path: Path) -> dict:
    data = path.read_bytes()
    ext = path.suffix.lower().lstrip(".")
    if ext == "docx":
        return summarize(docx_style.profile(data))
    if ext == "pdf":
        return summarize(pdf_style.profile(data))
    raise SystemExit(f"只支持 docx 与 pdf，收到 .{ext}")


def _spacing_pt(spacing: dict | None, size_pt: float | None) -> tuple[float | None, str]:
    """行距统一换算成磅值，并带回口径：固定值、倍数换算、还是从基线实测得来。"""
    if not spacing:
        return None, ""
    if spacing.get("pt") is not None:
        return float(spacing["pt"]), "实测基线距" if spacing.get("mode") == "measured" else "固定值"
    if spacing.get("mode") == "multiple" and size_pt:
        return round(spacing["value"] * size_pt * SINGLE_LINE_FACTOR, 1), f" {spacing['value']} 倍行距换算"
    return None, ""


def _diff(item: str, template, contract, ok: bool, note: str = "") -> dict:
    return {"item": item, "template": template, "contract": contract,
            "match": ok, **({"note": note} if note else {})}


def compare(contract: dict, template: dict) -> tuple[list[dict], list[dict]]:
    diffs: list[dict] = []
    skipped: list[dict] = []

    c_body, t_body = contract["body"], template["body"]

    for item, key in (("正文字体", "font"), ("正文字号", "size_pt")):
        if c_body[key] is None or t_body[key] is None:
            skipped.append({"item": item, "reason": "有一方取不到该项"})
            continue
        diffs.append(_diff(item, t_body[key], c_body[key], c_body[key] == t_body[key]))

    c_pt, c_mode = _spacing_pt(c_body["line_spacing"], c_body["size_pt"])
    t_pt, t_mode = _spacing_pt(t_body["line_spacing"], t_body["size_pt"])
    if c_pt is None or t_pt is None:
        skipped.append({"item": "行距", "reason": "有一方取不到行距"})
    else:
        note = f"模板取自{t_mode}、合同取自{c_mode}，差值在 {LINE_SPACING_TOLERANCE_PT} 磅内视为一致"
        diffs.append(_diff("行距（磅）", t_pt, c_pt, abs(c_pt - t_pt) <= LINE_SPACING_TOLERANCE_PT, note))

    c_page, t_page = contract["page"], template["page"]
    if c_page.get("size_mm") and t_page.get("size_mm"):
        diffs.append(_diff("页面尺寸 mm", t_page["size_mm"], c_page["size_mm"],
                           c_page["size_mm"] == t_page["size_mm"]))
    else:
        skipped.append({"item": "页面尺寸", "reason": "有一方取不到页面尺寸"})

    if c_page.get("margins_mm") and t_page.get("margins_mm"):
        approx = c_page.get("margins_approx") or t_page.get("margins_approx")
        missing = set(c_page.get("margins_unavailable", [])) | set(t_page.get("margins_unavailable", []))
        for side in ("top", "bottom", "left", "right"):
            if side in missing:
                skipped.append({"item": f"页边距 {side}", "reason": "PDF 无法推算该方向的边距"})
                continue
            c_value, t_value = c_page["margins_mm"].get(side), t_page["margins_mm"].get(side)
            if c_value is None or t_value is None:
                continue
            diffs.append(_diff(f"页边距 {side} mm", t_value, c_value,
                               abs(c_value - t_value) <= MARGIN_TOLERANCE_MM,
                               f"PDF 的页边距按文字起始位置推算，容差 {MARGIN_TOLERANCE_MM}mm" if approx else ""))
    else:
        skipped.append({"item": "页边距", "reason": "有一方取不到页边距"})

    for key in set(contract["unavailable"]) | set(template["unavailable"]):
        skipped.append({"item": key, "reason": f"{'合同' if key in contract['unavailable'] else '模板'}"
                                               f"（{contract['source'] if key in contract['unavailable'] else template['source']}）"
                                               f"取不到该项"})
    return diffs, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="docx / pdf 排版样式画像与比对")
    parser.add_argument("--path", "-p", required=True, help="待审查文件（docx 或 pdf）")
    parser.add_argument("--template", "-t", help="标准模板文件，给了才做比对")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.is_file():
        print(json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False))
        sys.exit(1)

    payload = {"contract": profile_file(path)}
    if args.template:
        template_path = Path(args.template)
        if not template_path.is_file():
            print(json.dumps({"error": f"模板不存在: {template_path}"}, ensure_ascii=False))
            sys.exit(1)
        payload["template"] = profile_file(template_path)
        payload["diffs"], payload["not_comparable"] = compare(payload["contract"], payload["template"])

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
