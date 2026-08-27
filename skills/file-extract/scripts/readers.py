#!/usr/bin/env python3
# /// script
# dependencies = []
# ///
"""
文档字节 → 纯文本。按扩展名分派，每个 reader 只负责抽取，不负责判断材料价值。

对外接口：
  read(kind, ext, data, max_chars, max_pages) -> {text, chars, truncated, ...}
  抽不出内容时抛 ExtractError，由调用方转成 errors[] 条目。

pdf 抽出文本极短时置 low_text=True（疑似扫描件无文本层），调用方据此决定是否走 OCR。

docx 只用标准库解析；pdf 用 pypdf，优先取环境里已装的，取不到则从 vendor/ 下的
wheel 直接 zipimport——沙箱完全离线，装不了任何包。
"""
import io
import pathlib
import re
import sys
import zipfile

# 低于这个字符数视为「没有文本层」，交给 OCR 兜底而不是当正文返回
MIN_PDF_CHARS = 80


class ExtractError(Exception):
    """单个文件抽取失败。带 kind 供调用方原样写进 errors[]。"""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def read(kind: str, ext: str, data: bytes, max_chars: int, max_pages: int = 100) -> dict:
    if ext == "pdf":
        return read_pdf(data, max_chars, max_pages)
    if ext == "docx":
        return read_docx(data, max_chars)
    if kind == "document":
        return read_plain(data, max_chars)
    raise ExtractError("unsupported_ext", f"没有对应的文本读取器: .{ext}")


def _finish(text: str, max_chars: int, extra: dict | None = None) -> dict:
    text = _squeeze(text)
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    result = {"text": text, "chars": len(text), "truncated": truncated}
    if extra:
        result.update(extra)
    return result


def _squeeze(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if not line and out and not out[-1]:
            continue
        out.append(line)
    return "\n".join(out).strip()


VENDOR_DIR = pathlib.Path(__file__).resolve().parent.parent / "vendor"


def load_pypdf():
    """取 PdfReader：先用环境里已装的 pypdf，没有就从 vendor/ 的 wheel 里 zipimport。

    wheel 本身就是 zip，纯 Python 包放进 sys.path 即可直接 import——沙箱离线装不了包，
    这是让 PDF 抽取在零依赖环境下仍然可用的唯一办法。
    """
    try:
        from pypdf import PdfReader
        return PdfReader
    except ImportError:
        pass

    wheels = sorted(VENDOR_DIR.glob("pypdf-*.whl"))
    if not wheels:
        raise ExtractError("parse_failed", f"pypdf 不可用，且 {VENDOR_DIR} 下没有 pypdf wheel")

    path = str(wheels[-1])
    if path not in sys.path:
        sys.path.insert(0, path)
    try:
        from pypdf import PdfReader
        return PdfReader
    except ImportError as exc:
        raise ExtractError("parse_failed", f"pypdf 不可用（vendor wheel 导入失败）: {exc}") from exc


def read_pdf(data: bytes, max_chars: int, max_pages: int) -> dict:
    PdfReader = load_pypdf()

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ExtractError("parse_failed", f"PDF 解析失败: {exc}") from exc

    if reader.is_encrypted:
        try:
            # 空密码能解开的是「仅限制编辑」的加密，正文照样可读
            if reader.decrypt("") == 0:
                raise ExtractError("parse_failed", "PDF 已加密，无法提取文本")
        except ExtractError:
            raise
        except Exception as exc:
            raise ExtractError("parse_failed", f"PDF 已加密且解密失败: {exc}") from exc

    total_pages = len(reader.pages)
    chunks: list[str] = []
    for index, page in enumerate(reader.pages[:max_pages]):
        try:
            chunks.append(page.extract_text() or "")
        except Exception as exc:
            chunks.append(f"[第 {index + 1} 页文本抽取失败: {exc}]")

    # low_text 必须按截断前的长度判定：--max-chars 调小时，截断后的 chars 会把
    # 正常合同误判成扫描件，白白多跑一遍 OCR。
    raw = _squeeze("\n".join(chunks))
    result = _finish(raw, max_chars, {
        "pages": total_pages,
        "pages_read": min(total_pages, max_pages)
    })
    result["low_text"] = len(raw) < MIN_PDF_CHARS
    return result


DOCUMENT_ENTRY = "word/document.xml"

# 标签一律匹配成 <w:x> 或 <w:x 属性>，不用 <w:x[^>]*>：docx 里存在 <w:text/>、<w:tcPr>
# 这类同前缀标签，宽松写法会把 <w:text/> 当成 <w:t> 的开标签，抽出来的「正文」里混满
# XML 属性串。
BLOCK_RE = re.compile(r"<w:tbl>[\s\S]*?</w:tbl>|<w:p(?:\s[^>]*)?/>|<w:p(?:\s[^>]*)?>[\s\S]*?</w:p>")
ROW_RE = re.compile(r"<w:tr(?:\s[^>]*)?>[\s\S]*?</w:tr>")
CELL_RE = re.compile(r"<w:tc(?:\s[^>]*)?>[\s\S]*?</w:tc>")
TEXT_RE = re.compile(r"<w:t(?:\s[^>]*)?>([\s\S]*?)</w:t>")

# 域代码（PAGE \* MERGEFORMAT 之类）与修订里被删掉的文字都不是正文
DROP_RE = re.compile(r"<w:instrText(?:\s[^>]*)?>[\s\S]*?</w:instrText>|<w:delText(?:\s[^>]*)?>[\s\S]*?</w:delText>")
TAB_RE = re.compile(r"<w:tab\s*/>")
# <w:br w:type="textWrapping"/> 这种带属性的软换行也要认，漏了会把上下两行黏成一行
BREAK_RE = re.compile(r"<w:br(?:\s[^>]*)?/?>")

# 内容控件在「未填写」状态下会把「单击此处输入文字。」这类占位文案作为正文存进 <w:t>，
# 靠 <w:showingPlcHdr/> 标记。整块丢掉，否则空白模板会被这串噪音填满。
# 只匹配不含嵌套 <w:sdt> 的最内层控件，避免非贪婪匹配把外层控件切在内层的结束标签上。
SDT_RE = re.compile(r"<w:sdt>(?:(?!<w:sdt>)[\s\S])*?</w:sdt>")
PLACEHOLDER_MARK = "<w:showingPlcHdr/>"

# 部分文档（WPS 存的居多）不写 showingPlcHdr，占位文案就以普通正文躺在控件里，
# 只能按字面识别。列表保持极短，只收 Word/WPS 中文版的默认占位文案。
PLACEHOLDER_TEXTS = {"单击此处输入文字。", "单击此处输入日期。"}

ENTITIES = {"&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'"}


def _decode_entities(text: str) -> str:
    text = re.sub(r"&(?:lt|gt|quot|apos);", lambda m: ENTITIES[m.group(0)], text)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    text = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), text)
    # &amp; 必须最后解，否则 &amp;lt; 会被二次解码成 <
    return text.replace("&amp;", "&")


def _is_placeholder(sdt: str) -> bool:
    return PLACEHOLDER_MARK in sdt or text_of(sdt).strip() in PLACEHOLDER_TEXTS


def drop_placeholders(xml: str) -> str:
    return SDT_RE.sub(lambda m: "" if _is_placeholder(m.group(0)) else m.group(0), xml)


def text_of(xml: str) -> str:
    normalized = TAB_RE.sub("<w:t>\t</w:t>", DROP_RE.sub("", xml))
    normalized = BREAK_RE.sub("<w:t>\n</w:t>", normalized)
    return _decode_entities("".join(TEXT_RE.findall(normalized)))


def read_docx(data: bytes, max_chars: int) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read(DOCUMENT_ENTRY).decode("utf-8", errors="replace")
    except KeyError as exc:
        raise ExtractError("parse_failed", f"docx 缺少 {DOCUMENT_ENTRY}") from exc
    except Exception as exc:
        raise ExtractError("parse_failed", f"docx 解析失败: {exc}") from exc

    # 占位控件可能整块包住 <w:tc>，标记在单元格之外，所以要在切块之前先清一遍
    xml = drop_placeholders(xml)

    lines: list[str] = []
    # 合同正文大量条款排在表格里，只取段落会整段丢失；按 <w:p> 与 <w:tbl> 的出现顺序
    # 逐块处理才能让段落与表格保持原文次序。
    for block in BLOCK_RE.findall(xml):
        if not block.startswith("<w:tbl>"):
            # 不 strip：条款里成串的空格是待填写的下划线区域，抹掉会看不出哪里是空档
            lines.append(text_of(block))
            continue
        for row in ROW_RE.findall(block):
            cells = [_cell_text(cell) for cell in CELL_RE.findall(row)]
            if any(cells):
                lines.append(" | ".join(cells))

    return _finish("\n".join(lines), max_chars)


def _cell_text(cell: str) -> str:
    """单元格文本。格内多段要用空格分开——签字页的「甲方」与「（盖章）」常是两个段落。"""
    paragraphs = [text_of(block) for block in BLOCK_RE.findall(cell)] or [text_of(cell)]
    return " ".join(paragraphs).strip()


def read_plain(data: bytes, max_chars: int) -> dict:
    for encoding in ("utf-8", "gbk", "utf-16"):
        try:
            return _finish(data.decode(encoding), max_chars)
        except UnicodeDecodeError:
            continue
    return _finish(data.decode("utf-8", errors="replace"), max_chars)
