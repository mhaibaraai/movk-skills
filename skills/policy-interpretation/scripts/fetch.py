#!/usr/bin/env python3
"""
抓取政务网页并清洗为正文文本。纯标准库，零外部依赖。

对外暴露两层能力：
  fetch_raw(url)  返回解码后的原始 HTML（search.py 用它正则提取列表页链接）
  fetch_one(url)  抓取 + 清洗为正文（供政策分析使用）

CLI:
  uv run scripts/fetch.py --urls '["https://...", "https://..."]'
  uv run scripts/fetch.py --urls '["https://..."]' --max-chars 5000

输出 JSON: [{url, title, text, length, truncated?, error?}]
"""

import argparse
import concurrent.futures
import gzip
import html.parser
import json
import re
import sys
import urllib.error
import urllib.request
import zlib

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# gb18030 是 gb2312/gbk 的严格超集，统一收敛过去以覆盖生僻字
_ENCODING_ALIASES = {"gb2312": "gb18030", "gbk": "gb18030"}
_FALLBACK_ENCODINGS = ("utf-8", "gb18030", "latin-1")

_CHARSET_RE = re.compile(rb"""charset=["']?\s*([\w-]+)""", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{3,}")

_SKIP_TAGS = frozenset({"script", "style", "nav", "footer", "header", "noscript"})


def _decompress(data: bytes, content_encoding: str) -> bytes:
    """政务网站普遍开启 gzip，urllib 不会自动解压。"""
    encoding = content_encoding.lower()
    if "gzip" in encoding:
        return gzip.decompress(data)
    if "deflate" in encoding:
        return zlib.decompress(data)
    return data


def _declared_charset(data: bytes, content_type: str) -> str | None:
    """先取响应头声明的编码，其次取 HTML 头部 meta 声明的编码。"""
    for source in (content_type.encode("ascii", "ignore"), data[:2048]):
        match = _CHARSET_RE.search(source)
        if match:
            charset = match.group(1).decode("ascii", "ignore").lower()
            return _ENCODING_ALIASES.get(charset, charset)
    return None


def _decode_text(data: bytes, content_type: str) -> str:
    declared = _declared_charset(data, content_type)
    for enc in (declared, *_FALLBACK_ENCODINGS):
        if not enc:
            continue
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def fetch_raw(url: str, timeout: int = 15) -> str:
    """抓取并返回解码后的原始 HTML（保留标签），TLS 证书始终校验。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        content_type = resp.headers.get("Content-Type", "")
        content_encoding = resp.headers.get("Content-Encoding", "")

    return _decode_text(_decompress(data, content_encoding), content_type)


class _TextExtractor(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            text = data.strip()
            if text:
                self.texts.append(text)


def clean_html(html_text: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html_text)
    return _BLANK_RE.sub("\n\n", "\n".join(extractor.texts)).strip()


def extract_title(html_text: str) -> str:
    match = _TITLE_RE.search(html_text)
    if match:
        return _TAG_RE.sub("", match.group(1)).strip()
    match = _H1_RE.search(html_text)
    if match:
        return _TAG_RE.sub("", match.group(1)).strip()
    return ""


def fetch_one(url: str, max_chars: int = 8000) -> dict:
    """抓取单个 URL 并返回清洗后的正文。"""
    try:
        html_text = fetch_raw(url)
    except urllib.error.HTTPError as e:
        return {"url": url, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"url": url, "error": f"请求失败: {e}"}

    if len(html_text) < 100:
        return {"url": url, "error": f"响应内容过小（{len(html_text)} 字符）"}

    text = clean_html(html_text)
    result = {
        "url": url,
        "title": extract_title(html_text),
        "length": len(text),
    }
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[... 截断，原文共 {len(text)} 字符 ...]"
        result["truncated"] = True
    result["text"] = text
    return result


def parallel_fetch(urls: list[str], max_chars: int, max_workers: int) -> list[dict]:
    deduped = list(dict.fromkeys(urls))
    print(f"抓取 {len(deduped)} 个页面（max_workers={max_workers}）...", file=sys.stderr)

    results: list[dict] = [{}] * len(deduped)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_one, u, max_chars): i for i, u in enumerate(deduped)}
        for future in concurrent.futures.as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = {"url": deduped[idx], "error": str(e)}

    succeeded = sum(1 for r in results if "error" not in r)
    total_chars = sum(r.get("length", 0) for r in results)
    print(
        f"成功 {succeeded} / 失败 {len(deduped) - succeeded} / 共 {total_chars} 字符",
        file=sys.stderr,
    )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="并行抓取多个政策页面，返回清洗后的正文")
    parser.add_argument("--urls", "-u", required=True, help="JSON 字符串数组")
    parser.add_argument("--max-chars", "-m", type=int, default=8000, help="单篇正文最大字符数")
    parser.add_argument("--max-workers", "-w", type=int, default=5)
    args = parser.parse_args()

    try:
        urls = json.loads(args.urls)
        if not isinstance(urls, list):
            raise ValueError("必须是 JSON 数组")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"--urls 解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    if not urls:
        print("[]")
        return

    print(json.dumps(parallel_fetch(urls, args.max_chars, args.max_workers), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
