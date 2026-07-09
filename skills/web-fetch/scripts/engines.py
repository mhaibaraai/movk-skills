#!/usr/bin/env python3
# /// script
# dependencies = ["pypdf>=4.0"]
# ///
"""
三层网页抓取引擎，按需自动降级：urllib（零依赖）→ curl_cffi（TLS 指纹伪装）→ playwright（真实浏览器）。

  urllib      标准库，零依赖，恒定可用。能抓服务端渲染的普通站点。
  curl_cffi   可选依赖，伪装 Chrome 的 TLS/JA3 指纹。用于解决"握手层"拦截
              （站点拒绝非浏览器 TLS 指纹，urllib 表现为 TLS 握手失败）。
  playwright  可选依赖，真实 Chromium 渲染。用于 JS 空壳页面与 Cloudflare
              等需要执行验证脚本的场景。需另外 `playwright install chromium`
              （或系统已装 Chrome，本模块会优先尝试 channel="chrome" 复用）。

engine="auto" 时按 urllib → curl_cffi → playwright 顺序尝试，命中以下任一情况
判定"未过关"从而升级到下一层：HTTP >= 400、响应体过小（JS 空壳特征）、
标题命中验证挑战页特征。curl_cffi / playwright 未安装时自动跳过对应层。

对外暴露：
  probe_engines()               各层可用性探测（含 chromium 是否可真正启动）
  fetch_bytes(url, engine)      抓取原始字节，返回 engine_used / tried 等元信息
  fetch_one(url, ...)           抓取 + 清洗，HTML 走正文提取、PDF 走文本抽取
  strip_tags(s) / clean_html(s) 供 search.py 复用的清洗工具
"""
from __future__ import annotations

import functools
import gzip
import html.parser
import io
import re
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
}
CHROME_ARGS = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]

MAX_BYTES = 30 * 1024 * 1024  # 超过 30MB 的响应直接拒绝，避免长报告附录拖垮沙箱
MIN_GOOD_BYTES = 500  # 低于此值多半是 JS 空壳（实测中海油首页仅 345 字节）

ENGINE_NAMES = ("urllib", "curl_cffi", "playwright")

# gb18030 是 gb2312/gbk 的严格超集，统一收敛过去以覆盖生僻字
_ENCODING_ALIASES = {"gb2312": "gb18030", "gbk": "gb18030"}
_FALLBACK_ENCODINGS = ("utf-8", "gb18030", "latin-1")

_CHARSET_RE = re.compile(rb"""charset=["']?\s*([\w-]+)""", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{3,}")
_CHALLENGE_RE = re.compile(r"Just a moment|Attention Required|请稍候|安全验证|verify you are human", re.I)

_SKIP_TAGS = frozenset({"script", "style", "nav", "footer", "header", "noscript"})


class ContentTooLarge(Exception):
    def __init__(self, size_bytes: int) -> None:
        self.size_bytes = size_bytes
        super().__init__(f"内容体积 {size_bytes / 1024 / 1024:.1f}MB 超过上限")


# ── 编码与 HTML 清洗（与 policy-interpretation/fetch.py 同源逻辑） ──────────

def _decompress(data: bytes, content_encoding: str) -> bytes:
    encoding = (content_encoding or "").lower()
    if "gzip" in encoding:
        try:
            return gzip.decompress(data)
        except OSError:
            return data
    if "deflate" in encoding:
        try:
            return zlib.decompress(data)
        except zlib.error:
            return data
    return data


def _declared_charset(data: bytes, content_type: str) -> str | None:
    for source in ((content_type or "").encode("ascii", "ignore"), data[:2048]):
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


def strip_tags(text: str) -> str:
    """去掉标签并反转义实体，用于清洗标题与搜索结果的 <em> 高亮片段。"""
    return html.unescape(_TAG_RE.sub("", text)).strip()


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
    for pattern in (_TITLE_RE, _H1_RE):
        match = pattern.search(html_text)
        if match:
            return strip_tags(match.group(1))
    return ""


# ── 引擎可用性探测 ──────────────────────────────────────────────────────

def _launch_chromium(p):
    """优先复用系统 Chrome（channel="chrome"），省去下载 chromium 二进制。"""
    try:
        return p.chromium.launch(headless=True, channel="chrome", args=CHROME_ARGS)
    except Exception:
        return p.chromium.launch(headless=True, args=CHROME_ARGS)


@functools.lru_cache(maxsize=1)
def probe_engines() -> dict:
    """实测各层是否真正可用（而非仅猜测依赖是否安装），结果按进程缓存一次。"""
    avail = {"urllib": True, "curl_cffi": False, "playwright": False, "chromium": False}

    try:
        import curl_cffi  # noqa: F401
        avail["curl_cffi"] = True
    except ImportError:
        pass

    try:
        from playwright.sync_api import sync_playwright
        avail["playwright"] = True
        with sync_playwright() as p:
            try:
                browser = _launch_chromium(p)
                browser.close()
                avail["chromium"] = True
            except Exception:
                avail["chromium"] = False
    except ImportError:
        pass

    return avail


# ── 各层抓取实现，统一签名 (url, timeout) -> (data: bytes, content_type: str, status: int) ──

def _fetch_urllib(url: str, timeout: int) -> tuple[bytes, str, int]:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        status = resp.status
        headers = resp.headers
        reader = resp
    except urllib.error.HTTPError as e:
        status = e.code
        headers = e.headers
        reader = e

    content_length = headers.get("Content-Length")
    if content_length and int(content_length) > MAX_BYTES:
        raise ContentTooLarge(int(content_length))

    data = reader.read()
    content_type = headers.get("Content-Type", "")
    content_encoding = headers.get("Content-Encoding", "")
    return _decompress(data, content_encoding), content_type, status


def _fetch_curl_cffi(url: str, timeout: int) -> tuple[bytes, str, int]:
    from curl_cffi import requests as cffi_requests

    r = cffi_requests.get(url, headers=HEADERS, impersonate="chrome", timeout=timeout, allow_redirects=True)
    content_length = r.headers.get("Content-Length")
    if content_length and int(content_length) > MAX_BYTES:
        raise ContentTooLarge(int(content_length))
    return r.content, r.headers.get("Content-Type", ""), r.status_code


def _wait_for_challenge(page, max_attempts: int = 20) -> bool:
    """等待 Cloudflare 等验证挑战页通过，逻辑借鉴 chem-safety-query/common.py。"""
    for _ in range(max_attempts):
        try:
            title = page.title()
        except Exception:
            title = ""
        challenge = bool(_CHALLENGE_RE.search(title))
        if not challenge:
            try:
                challenge = page.locator("#challenge-running").is_visible()
            except Exception:
                pass
        if not challenge:
            return True
        time.sleep(1)
    return False


def _fetch_playwright(url: str, timeout: int) -> tuple[bytes, str, int]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch_chromium(p)
        try:
            context = browser.new_context(user_agent=USER_AGENT, locale="zh-CN")
            page = context.new_page()
            response = page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            status = response.status if response else 0
            content_type = response.headers.get("content-type", "") if response else ""

            if "pdf" in content_type.lower():
                data = response.body()
            else:
                _wait_for_challenge(page)
                data = page.content().encode("utf-8")
                if not content_type:
                    content_type = "text/html; charset=utf-8"
            return data, content_type, status
        finally:
            browser.close()


_ENGINE_FUNCS = {
    "urllib": _fetch_urllib,
    "curl_cffi": _fetch_curl_cffi,
    "playwright": _fetch_playwright,
}


def _looks_blocked(status: int, data: bytes, content_type: str) -> bool:
    if status and status >= 400:
        return True
    ct = (content_type or "").lower()
    if "pdf" in ct:
        return False
    if len(data) < MIN_GOOD_BYTES:
        return True
    sample = data[:4096].decode("utf-8", errors="ignore")
    return bool(_CHALLENGE_RE.search(sample))


def fetch_bytes(url: str, engine: str = "auto", timeout: int = 20) -> dict:
    """抓取原始字节，自动按 urllib -> curl_cffi -> playwright 升级直到拿到可用内容。

    返回：
      成功 {"data": bytes, "content_type": str, "status": int,
            "engine_used": str, "tried": [...]}
      失败 {"data": None, "error": str, "tried": [...], "engines_available": {...}}
    """
    avail = probe_engines()
    chain = [engine] if engine != "auto" else list(ENGINE_NAMES)
    tried: list[str] = []
    last_error: str | None = None

    for eng in chain:
        if eng == "curl_cffi" and not avail["curl_cffi"]:
            continue
        if eng == "playwright" and not (avail["playwright"] and avail["chromium"]):
            continue

        tried.append(eng)
        try:
            data, content_type, status = _ENGINE_FUNCS[eng](url, timeout)
        except ContentTooLarge as e:
            return {
                "data": None,
                "error": f"内容过大（{e.size_bytes / 1024 / 1024:.1f}MB），已跳过下载",
                "tried": tried,
                "engines_available": avail,
            }
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            continue

        if not _looks_blocked(status, data, content_type):
            return {
                "data": data,
                "content_type": content_type,
                "status": status,
                "engine_used": eng,
                "tried": tried,
            }
        last_error = f"HTTP {status}，疑似反爬/验证页（{eng} 层）"

    return {
        "data": None,
        "error": last_error or "所有可用引擎均失败",
        "tried": tried,
        "engines_available": avail,
    }


# ── PDF 文本抽取 ──────────────────────────────────────────────────────────

def _extract_pdf(data: bytes, max_chars: int, max_pages: int) -> dict:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:
        return {"type": "pdf", "error": f"PDF 解析失败: {e}", "low_confidence": True}

    if reader.is_encrypted:
        try:
            if reader.decrypt("") == 0:
                return {"type": "pdf", "error": "PDF 已加密，无法提取文本，建议改用其他方式获取原文",
                         "low_confidence": True}
        except Exception:
            return {"type": "pdf", "error": "PDF 已加密，无法提取文本", "low_confidence": True}

    total_pages = len(reader.pages)
    read_pages = reader.pages[:max_pages]
    parts = []
    for page in read_pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            parts.append("")
    text = "\n\n".join(parts).strip()

    title = ""
    try:
        if reader.metadata and reader.metadata.title:
            title = str(reader.metadata.title).strip()
    except Exception:
        pass
    if not title and text:
        title = text.splitlines()[0][:80]

    result = {"type": "pdf", "pages": total_pages, "pages_read": len(read_pages),
              "title": title, "length": len(text)}

    if len(text) < 50:
        result["low_confidence"] = True
        result["error"] = "PDF 抽取文本过短，疑似扫描件无文本层，建议改用其他方式获取原文"

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[... 截断，原文共 {len(text)} 字符 ...]"
        result["truncated"] = True
    result["text"] = text
    return result


# ── 统一入口：抓取 + 清洗 ──────────────────────────────────────────────────

def fetch_one(url: str, max_chars: int = 8000, max_pages: int = 30, engine: str = "auto",
              timeout: int = 20) -> dict:
    fetched = fetch_bytes(url, engine=engine, timeout=timeout)
    if fetched.get("data") is None:
        result = {"url": url, "error": fetched["error"], "tried": fetched.get("tried", [])}
        if "engines_available" in fetched:
            result["engines_available"] = fetched["engines_available"]
        return result

    data = fetched["data"]
    content_type = fetched.get("content_type") or ""
    engine_used = fetched["engine_used"]
    degraded = engine_used != "urllib"

    if "pdf" in content_type.lower():
        pdf_result = _extract_pdf(data, max_chars, max_pages)
        pdf_result.update({"url": url, "engine_used": engine_used, "degraded": degraded})
        return pdf_result

    html_text = _decode_text(data, content_type)
    if len(html_text) < 100:
        return {"url": url, "error": f"响应内容过小（{len(html_text)} 字符）",
                 "engine_used": engine_used, "degraded": degraded}

    text = clean_html(html_text)
    result = {
        "url": url,
        "engine_used": engine_used,
        "type": "html",
        "title": extract_title(html_text),
        "length": len(text),
        "degraded": degraded,
    }
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[... 截断，原文共 {len(text)} 字符 ...]"
        result["truncated"] = True
    result["text"] = text
    return result
