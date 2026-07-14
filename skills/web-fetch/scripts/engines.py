#!/usr/bin/env python3
# /// script
# dependencies = ["pypdf>=4.0", "curl_cffi>=0.7"]
# ///
"""
四层网页抓取引擎，按需自动降级：urllib（零依赖）→ curl_cffi（TLS 指纹伪装）
→ reader_proxy（远端渲染代理）→ playwright（本地真实浏览器）。

  urllib       标准库，零依赖，恒定可用。能抓服务端渲染的普通站点。
  curl_cffi    伪装 Chrome 的 TLS/JA3 指纹。用于解决"握手层"拦截（站点拒绝
               非浏览器 TLS 指纹，urllib 表现为 TLS 握手失败）。已声明为硬依赖。
  reader_proxy 远端渲染代理（默认 r.jina.ai），由对方跑无头浏览器执行 JS 后
               把渲染结果吐回来，本地只需一个 urllib GET。用于本地装不了浏览器
               的沙箱环境，是 JS 挑战站点（瑞数/加速乐/Cloudflare）的主力手段。
  playwright   本地真实 Chromium 渲染，链尾兜底。前三层都不过时才按需安装
               （chromium 二进制约 150MB），装不上就如实报错。

engine="auto" 时按上述顺序尝试，命中以下任一情况判定"未过关"从而升级到下一层：
HTTP >= 400、响应体过小（JS 空壳特征）、命中验证挑战页特征。

环境变量：
  JINA_API_KEY                 配置后提升 r.jina.ai 配额，不配也能用（有速率限制）
  WEB_FETCH_READER_ENDPOINT    覆盖渲染代理端点（自建或换供应商）
  WEB_FETCH_NO_READER_PROXY    置 1 禁用远端代理层（目标 URL 不外发给第三方）
  WEB_FETCH_NO_AUTO_INSTALL    置 1 禁止自动安装 playwright/chromium

对外暴露：
  probe_engines()               各层可用性探测（含 chromium 是否可真正启动）
  fetch_bytes(url, engine)      抓取原始字节，返回 engine_used / tried 等元信息
  fetch_one(url, ...)           抓取 + 清洗，HTML 走正文提取、PDF 走文本抽取
  fetch_raw(url, ...)           抓取 + 解码，不清洗不截断，供需要自行解析结构的调用方使用
  strip_tags(s) / clean_html(s) 供 search.py 复用的清洗工具
"""
from __future__ import annotations

import functools
import gzip
import html.parser
import io
import ipaddress
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

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

ENGINE_NAMES = ("urllib", "curl_cffi", "reader_proxy", "playwright")

READER_ENDPOINT = "https://r.jina.ai/"
READER_MIN_TIMEOUT = 60  # 代理要在远端跑完整浏览器渲染，比直连慢得多
INSTALL_TIMEOUT = 900  # chromium 二进制约 150MB，冷环境下载可能数分钟

# gb18030 是 gb2312/gbk 的严格超集，统一收敛过去以覆盖生僻字
_ENCODING_ALIASES = {"gb2312": "gb18030", "gbk": "gb18030"}
_FALLBACK_ENCODINGS = ("utf-8", "gb18030", "latin-1")

_CHARSET_RE = re.compile(rb"""charset=["']?\s*([\w-]+)""", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{3,}")
# $_ts / __jsluid 分别是瑞数与加速乐的 JS 挑战特征，这类站点可能以 200 返回挑战页
_CHALLENGE_RE = re.compile(
    r"Just a moment|Attention Required|请稍候|安全验证|verify you are human|\$_ts|__jsluid",
    re.I,
)

_SKIP_TAGS = frozenset({"script", "style", "nav", "footer", "header", "noscript"})


class ContentTooLarge(Exception):
    def __init__(self, size_bytes: int) -> None:
        self.size_bytes = size_bytes
        super().__init__(f"内容体积 {size_bytes / 1024 / 1024:.1f}MB 超过上限")


class ProxyRefused(Exception):
    """目标不允许经第三方渲染代理抓取（内网地址）。"""


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


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
    """实测各层是否真正可用（而非仅猜测依赖是否安装），结果按进程缓存一次。

    reader_proxy 不做网络探测（否则每次抓取都要多打一个请求），只看是否被显式禁用。
    """
    avail = {
        "urllib": True,
        "curl_cffi": False,
        "reader_proxy": not _env_on("WEB_FETCH_NO_READER_PROXY"),
        "playwright": False,
        "chromium": False,
    }

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


# ── playwright 按需安装：仅在前三层都不过关时触发 ────────────────────────────

_install_attempted = False


def _run(cmd: list[str]) -> bool:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=INSTALL_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"  失败（{type(e).__name__}）：{' '.join(cmd)}", file=sys.stderr)
        return False
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        print(f"  失败：{' '.join(cmd)}", file=sys.stderr)
        for line in tail:
            print(f"    {line}", file=sys.stderr)
        return False
    return True


def _install_playwright_package() -> bool:
    """uv run 建的临时环境默认不带 pip，所以优先走 uv pip install。"""
    if shutil.which("uv") and _run(["uv", "pip", "install", "--python", sys.executable, "playwright"]):
        return True
    return _run([sys.executable, "-m", "pip", "install", "playwright"])


def _ensure_playwright() -> bool:
    """确保 playwright 与 chromium 可用，必要时下载安装。一个进程只尝试一次。"""
    global _install_attempted

    avail = probe_engines()
    if avail["playwright"] and avail["chromium"]:
        return True
    if _install_attempted or _env_on("WEB_FETCH_NO_AUTO_INSTALL"):
        return False
    _install_attempted = True

    if not avail["playwright"]:
        print("前三层均未过关，开始安装 playwright...", file=sys.stderr)
        if not _install_playwright_package():
            print("  手动安装：uv pip install playwright && playwright install chromium", file=sys.stderr)
            return False

    print("下载 chromium（约 150MB，可能耗时数分钟）...", file=sys.stderr)
    if not _run([sys.executable, "-m", "playwright", "install", "chromium"]):
        print("  手动安装：playwright install chromium", file=sys.stderr)
        return False

    probe_engines.cache_clear()
    avail = probe_engines()
    return avail["playwright"] and avail["chromium"]


# ── 各层抓取实现，统一签名 (url, timeout) -> (data: bytes, content_type: str, status: int) ──

def _http_get(url: str, headers: dict[str, str], timeout: int) -> tuple[bytes, str, int]:
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        status = resp.status
        resp_headers = resp.headers
        reader = resp
    except urllib.error.HTTPError as e:
        status = e.code
        resp_headers = e.headers
        reader = e

    content_length = resp_headers.get("Content-Length")
    if content_length and int(content_length) > MAX_BYTES:
        raise ContentTooLarge(int(content_length))

    data = reader.read()
    content_type = resp_headers.get("Content-Type", "")
    content_encoding = resp_headers.get("Content-Encoding", "")
    return _decompress(data, content_encoding), content_type, status


def _fetch_urllib(url: str, timeout: int) -> tuple[bytes, str, int]:
    return _http_get(url, HEADERS, timeout)


def _is_private_target(url: str) -> bool:
    """内网/本地地址不得外发给第三方代理。无法解析的 host 一律按私有处理。"""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not host or host == "localhost" or host.endswith((".local", ".internal", ".localdomain")):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def _reader_proxy_url(target: str) -> str:
    endpoint = (os.environ.get("WEB_FETCH_READER_ENDPOINT") or READER_ENDPOINT).strip()
    return f"{endpoint.rstrip('/')}/{target}"


def _reader_proxy_headers() -> dict[str, str]:
    # 要渲染后的 HTML 而非默认的 markdown，好让结果直接复用本模块的清洗与 raw 管线
    headers = {**HEADERS, "X-Return-Format": "html"}
    api_key = (os.environ.get("JINA_API_KEY") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _fetch_reader_proxy(url: str, timeout: int) -> tuple[bytes, str, int]:
    """交给远端渲染代理执行 JS，本地只发一个普通 GET。"""
    if _is_private_target(url):
        raise ProxyRefused(f"内网/本地地址不外发给渲染代理：{url}")

    data, content_type, status = _http_get(
        _reader_proxy_url(url), _reader_proxy_headers(), max(timeout, READER_MIN_TIMEOUT)
    )
    # 代理以 text/plain 回吐渲染后的 HTML，纠正 content_type 才能走 HTML 分支
    if "html" not in content_type.lower() and data[:200].lstrip().startswith(b"<"):
        content_type = "text/html; charset=utf-8"
    return data, content_type, status


def _fetch_curl_cffi(url: str, timeout: int) -> tuple[bytes, str, int]:
    from curl_cffi import requests as cffi_requests

    r = cffi_requests.get(url, headers=HEADERS, impersonate="chrome", timeout=timeout, allow_redirects=True)
    content_length = r.headers.get("Content-Length")
    if content_length and int(content_length) > MAX_BYTES:
        raise ContentTooLarge(int(content_length))
    return r.content, r.headers.get("Content-Type", ""), r.status_code


def _wait_for_challenge(page, max_attempts: int = 20) -> bool:
    """等挑战页把 JS 跑完并重载出真内容。

    瑞数这类防护首跳返回 412 + 混淆脚本，脚本执行后写 cookie 再自行重载才吐真内容，
    所以不能只看标题——挑战特征藏在页面正文里。
    """
    for _ in range(max_attempts):
        try:
            title = page.title()
            content = page.content()
        except Exception:
            time.sleep(1)  # 页面正在重载，取不到内容不等于挑战已通过
            continue

        challenge = bool(_CHALLENGE_RE.search(title) or _CHALLENGE_RE.search(content[:4096]))
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
                return response.body(), content_type, status

            passed = _wait_for_challenge(page)
            data = page.content().encode("utf-8")
            if not content_type:
                content_type = "text/html; charset=utf-8"
            # 挑战通过后页面已重载出真内容，首跳的 4xx 不代表最终结果
            if passed and status >= 400 and not _looks_blocked(0, data, content_type):
                status = 200
            return data, content_type, status
        finally:
            browser.close()


_ENGINE_FUNCS = {
    "urllib": _fetch_urllib,
    "curl_cffi": _fetch_curl_cffi,
    "reader_proxy": _fetch_reader_proxy,
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


def _engine_ready(eng: str, avail: dict) -> bool:
    if eng == "curl_cffi":
        return avail["curl_cffi"]
    if eng == "reader_proxy":
        return avail["reader_proxy"]
    if eng == "playwright":
        return _ensure_playwright()  # 链尾兜底，缺 chromium 时按需下载
    return True


def fetch_bytes(url: str, engine: str = "auto", timeout: int = 20) -> dict:
    """抓取原始字节，自动按 urllib -> curl_cffi -> reader_proxy -> playwright 升级直到拿到可用内容。

    返回：
      成功 {"data": bytes, "content_type": str, "status": int,
            "engine_used": str, "tried": [...]}
      失败 {"data": None, "error": str, "tried": [...], "engines_available": {...}}
    """
    chain = [engine] if engine != "auto" else list(ENGINE_NAMES)
    tried: list[str] = []
    last_error: str | None = None

    for eng in chain:
        if not _engine_ready(eng, probe_engines()):
            continue

        tried.append(eng)
        try:
            data, content_type, status = _ENGINE_FUNCS[eng](url, timeout)
        except ContentTooLarge as e:
            return {
                "data": None,
                "error": f"内容过大（{e.size_bytes / 1024 / 1024:.1f}MB），已跳过下载",
                "tried": tried,
                "engines_available": probe_engines(),
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
        last_error = f"HTTP {status}，疑似反爬/JS 挑战页（{eng} 层）"

    return {
        "data": None,
        "error": last_error or "所有可用引擎均失败",
        "tried": tried,
        "engines_available": probe_engines(),
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

def _failure(url: str, fetched: dict) -> dict:
    result = {"url": url, "error": fetched["error"], "tried": fetched.get("tried", [])}
    if "engines_available" in fetched:
        result["engines_available"] = fetched["engines_available"]
    return result


def fetch_raw(url: str, engine: str = "auto", timeout: int = 20) -> dict:
    """抓取并解码原始响应体，不做正文清洗也不截断。

    供需要自行解析结构的调用方使用（如政策文件库的 JSON 接口、需要正则提取 href 的列表页），
    清洗与截断都会破坏这类解析。二进制内容（PDF）不适用，请改用 fetch_one。
    """
    fetched = fetch_bytes(url, engine=engine, timeout=timeout)
    if fetched.get("data") is None:
        return _failure(url, fetched)

    content_type = fetched.get("content_type") or ""
    if "pdf" in content_type.lower():
        return {"url": url, "error": "raw 模式不支持 PDF，请改用默认模式抽取文本",
                "tried": fetched.get("tried", [])}

    raw = _decode_text(fetched["data"], content_type)
    engine_used = fetched["engine_used"]
    return {
        "url": url,
        "engine_used": engine_used,
        "status": fetched.get("status", 0),
        "content_type": content_type,
        "degraded": engine_used != "urllib",
        "length": len(raw),
        "raw": raw,
    }


def fetch_one(url: str, max_chars: int = 8000, max_pages: int = 30, engine: str = "auto",
              timeout: int = 20) -> dict:
    fetched = fetch_bytes(url, engine=engine, timeout=timeout)
    if fetched.get("data") is None:
        return _failure(url, fetched)

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
