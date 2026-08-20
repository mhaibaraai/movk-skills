#!/usr/bin/env python3
# /// script
# dependencies = ["pypdf>=4.0", "python-docx>=1.1"]
# ///
"""
文档字节 → 纯文本。按扩展名分派，每个 reader 只负责抽取，不负责判断材料价值。

对外接口：
  read(kind, ext, data, max_chars, max_pages) -> {text, chars, truncated, ...}
  抽不出内容时抛 ExtractError，由调用方转成 errors[] 条目。

pdf 抽出文本极短时置 low_text=True（疑似扫描件无文本层），调用方据此决定是否走 OCR。
"""
import io

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


def read_pdf(data: bytes, max_chars: int, max_pages: int) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ExtractError("parse_failed", f"pypdf 不可用: {exc}") from exc

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


def read_docx(data: bytes, max_chars: int) -> dict:
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise ExtractError("parse_failed", f"python-docx 不可用: {exc}") from exc

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ExtractError("parse_failed", f"docx 解析失败: {exc}") from exc

    lines: list[str] = []
    # 合同正文大量条款排在表格里，只取 paragraphs 会整段丢失；按 body 子元素顺序遍历
    # 才能让段落与表格保持原文次序。
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            lines.append(Paragraph(child, document).text)
        elif tag == "tbl":
            for row in Table(child, document).rows:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))

    return _finish("\n".join(lines), max_chars)


def read_plain(data: bytes, max_chars: int) -> dict:
    for encoding in ("utf-8", "gbk", "utf-16"):
        try:
            return _finish(data.decode(encoding), max_chars)
        except UnicodeDecodeError:
            continue
    return _finish(data.decode("utf-8", errors="replace"), max_chars)
