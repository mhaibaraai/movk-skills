#!/usr/bin/env python3
# /// script
# dependencies = []
# ///
"""
docx 排版样式画像。只用标准库。

docx 的段落格式是继承来的：docDefaults → 默认段落样式 → pStyle（可经 basedOn 逐级
上溯）→ 段落自身的 pPr/rPr。实测模板里超过七成段落的字号在直接格式里是空的，
不算继承链就只能得到一堆 null，所以这里必须把整条链算出来。

对外接口：
  profile(data: bytes) -> dict   与 pdf_style.profile 同构的样式画像
"""
import io
import re
import zipfile

import readers

STYLES_ENTRY = "word/styles.xml"
DOCUMENT_ENTRY = "word/document.xml"

RUN_RE = re.compile(r"<w:r(?:\s[^>]*)?>[\s\S]*?</w:r>")
RPR_RE = re.compile(r"<w:rPr>[\s\S]*?</w:rPr>")
PPR_RE = re.compile(r"<w:pPr>[\s\S]*?</w:pPr>")
STYLE_RE = re.compile(r"<w:style(?:\s[^>]*)?>[\s\S]*?</w:style>")
SECTPR_RE = re.compile(r"<w:sectPr(?:\s[^>]*)?>[\s\S]*?</w:sectPr>")

# 缇（1/20 磅）→ 毫米
TWIP_TO_MM = 25.4 / 72 / 20


def _attr(xml: str, tag: str, key: str) -> str | None:
    match = re.search(rf"<{tag}(?:\s[^>]*)?\s{key}=\"([^\"]+)\"", xml)
    return match.group(1) if match else None


def _num(value: str | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _run_format(xml: str) -> dict:
    """<w:rPr> → 字体、字号、加粗。空值表示「此层未指定」，留给继承链上层填。"""
    size = _num(_attr(xml, "w:sz", "w:val"))
    return {
        # 中文合同看的是 eastAsia，西文名只在没有中文字体时才有意义
        "font": _attr(xml, "w:rFonts", "w:eastAsia") or _attr(xml, "w:rFonts", "w:ascii"),
        "size_pt": size / 2 if size else None,     # w:sz 的值是磅×2
        "bold": True if re.search(r"<w:b/>|<w:b\s[^>]*/>", xml) else None
    }


def _paragraph_format(xml: str) -> dict:
    """<w:pPr> → 对齐、行距、首行缩进。"""
    line = _num(_attr(xml, "w:spacing", "w:line"))
    rule = _attr(xml, "w:spacing", "w:lineRule") or "auto"
    spacing = None
    if line:
        # auto 是倍数（值 = 倍数×240），exact / atLeast 是固定磅值（值 = 磅×20）
        spacing = ({"mode": "multiple", "value": round(line / 240, 2)} if rule == "auto"
                   else {"mode": rule, "pt": round(line / 20, 1)})

    chars = _num(_attr(xml, "w:ind", "w:firstLineChars"))
    twips = _num(_attr(xml, "w:ind", "w:firstLine"))
    indent = None
    if chars:
        indent = {"mode": "chars", "value": round(chars / 100, 2)}
    elif twips:
        indent = {"mode": "pt", "value": round(twips / 20, 1)}

    return {
        "align": _attr(xml, "w:jc", "w:val"),
        "line_spacing": spacing,
        "first_line_indent": indent
    }


def _merge(base: dict, layer: dict) -> dict:
    """继承链的一层叠加：只有明确给了值的键才覆盖上层。"""
    return {**base, **{k: v for k, v in layer.items() if v is not None}}


class StyleSheet:
    """styles.xml 的继承链解析器。"""

    def __init__(self, xml: str):
        self.styles: dict[str, dict] = {}
        self.default_paragraph = ""
        self._cache: dict[str, dict] = {}

        defaults = re.search(r"<w:docDefaults>[\s\S]*?</w:docDefaults>", xml)
        default_xml = defaults.group(0) if defaults else ""
        self.doc_defaults = {**_run_format(default_xml), **_paragraph_format(default_xml)}

        for style in STYLE_RE.findall(xml):
            style_id = _attr(style, "w:style", "w:styleId")
            if not style_id:
                continue
            rpr = RPR_RE.search(style)
            ppr = PPR_RE.search(style)
            self.styles[style_id] = {
                "basedOn": _attr(style, "w:basedOn", "w:val"),
                "format": {
                    **_run_format(rpr.group(0) if rpr else ""),
                    **_paragraph_format(ppr.group(0) if ppr else "")
                }
            }
            if 'w:type="paragraph"' in style and 'w:default="1"' in style:
                self.default_paragraph = style_id

    def resolve(self, style_id: str | None) -> dict:
        """算出某个样式的完整格式：docDefaults → 默认段落样式 → basedOn 链 → 样式自身。

        没有 pStyle 的段落也要继承默认段落样式（Normal）——正文字号通常只写在那里，
        漏掉这一层的话大部分段落都会拿不到字号。
        """
        root = dict(self.doc_defaults)
        if self.default_paragraph and style_id != self.default_paragraph:
            root = self._chain(self.default_paragraph, root)
        return root if not style_id else self._chain(style_id, root)

    def _chain(self, style_id: str | None, root: dict) -> dict:
        if not style_id or style_id not in self.styles:
            return dict(root)
        if style_id in self._cache:
            return dict(self._cache[style_id])

        # 先占位再递归，basedOn 万一成环也不会把自己套死
        self._cache[style_id] = root
        style = self.styles[style_id]
        parent = self._chain(style["basedOn"], root) if style["basedOn"] else root
        merged = _merge(parent, style["format"])
        self._cache[style_id] = merged
        return dict(merged)


def _page(xml: str) -> dict:
    sect = SECTPR_RE.search(xml)
    if not sect:
        return {}
    body = sect.group(0)
    width = _num(_attr(body, "w:pgSz", "w:w"))
    height = _num(_attr(body, "w:pgSz", "w:h"))
    margins = {
        key: round(_num(_attr(body, "w:pgMar", f"w:{key}")) * TWIP_TO_MM, 1)
        for key in ("top", "bottom", "left", "right")
        if _num(_attr(body, "w:pgMar", f"w:{key}")) is not None
    }
    page = {"margins_mm": margins} if margins else {}
    if width and height:
        page["size_mm"] = [round(width * TWIP_TO_MM, 1), round(height * TWIP_TO_MM, 1)]
    return page


def profile(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = archive.read(DOCUMENT_ENTRY).decode("utf-8", errors="replace")
        try:
            sheet = StyleSheet(archive.read(STYLES_ENTRY).decode("utf-8", errors="replace"))
        except KeyError:
            sheet = StyleSheet("")

    document = readers.drop_placeholders(document)
    runs: list[dict] = []
    paragraphs: list[dict] = []

    for block in readers.BLOCK_RE.findall(document):
        ppr = PPR_RE.search(block)
        ppr_xml = ppr.group(0) if ppr else ""
        style_id = _attr(ppr_xml, "w:pStyle", "w:val")
        paragraph_format = _merge(sheet.resolve(style_id),
                                  {**_run_format(ppr_xml), **_paragraph_format(ppr_xml)})

        chars = 0
        for run in RUN_RE.findall(block):
            text = readers.text_of(run)
            if not text.strip():
                continue
            rpr = RPR_RE.search(run)
            fmt = _merge(paragraph_format, _run_format(rpr.group(0) if rpr else ""))
            runs.append({**fmt, "chars": len(text.strip())})
            chars += len(text.strip())

        text = readers.text_of(block).strip()
        if text and chars:
            paragraphs.append({**paragraph_format, "text": text, "chars": chars})

    return {"source": "docx", "page": _page(document), "runs": runs, "paragraphs": paragraphs}
