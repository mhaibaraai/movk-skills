#!/usr/bin/env python3
# /// script
# dependencies = ["pypdf>=4.0", "curl_cffi>=0.7"]
# ///
"""
两层网页抓取引擎，按需降级：http（curl_cffi）→ browser（playwright）。

  http     curl_cffi 直发请求并伪装 Chrome 的 TLS/JA3 指纹。服务端渲染的站点一个请求就拿到，
           也是所有非 HTML 资源（PDF / sitemap.xml / robots.txt / JSON）的唯一取法——浏览器
           goto 一个 PDF 会抛 "Download is starting"，它是获取字节的错误工具。
  browser  playwright 真实 Chromium 渲染，单浏览器实例复用、多 page 并发。用于 http 层被挡下
           的 HTML：JS 空壳（中海油）、必须执行 JS 才吐正文的页面、搜索引擎结果页。链尾按需
           安装（只装 headless shell，完整 chromium 全程用不到），装不上就如实报错。

判定"抓到了"用的是正向依据：渲染后必须有实质正文（MIN_TEXT_CHARS）。不能反过来用"挑战特征
消失"判定通过——瑞数把页面清空后特征也随之消失，会把空壳当成功。

能力边界：瑞数一类的 JS 挑战本地浏览器未必过得去（www.cnpc.com.cn 实测过不去），此时如实
报错，不返回空壳正文让调用方拿去分析。

环境变量：
  WEB_FETCH_NO_AUTO_INSTALL   置 1 禁止自动安装 playwright/chromium

对外暴露：
  probe_engines()               各层可用性探测（含 chromium 是否能真正启动）
  fetch_bytes(url, engine)      抓取单个 URL 的原始字节，返回 engine_used / attempts 等元信息
  fetch_one(url, ...)           抓取 + 清洗，HTML 走正文提取、PDF 走文本抽取
  fetch_raw(url, ...)           抓取 + 解码，不清洗不截断，供调用方自行解析结构
  fetch_many(urls, ...)         批量抓取，http 层并发试完后，剩下的共用一个浏览器实例
  strip_tags(s) / clean_html(s) 供 search.py 复用的清洗工具
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import gzip
import html.parser
import io
import os
import re
import shutil
import subprocess
import sys
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
MIN_TEXT_CHARS = 200  # 清洗/渲染后正文低于此视为没抓到——瑞数空壳只剩一个标题

ENGINE_NAMES = ("http", "browser")

BROWSER_MIN_TIMEOUT = 45  # 实测 so.com 首跳 19.4s、news.cnpc.com.cn 31.6s，20s 必然误杀
DEFAULT_CONCURRENCY = 5  # http 层的线程数，也是同一浏览器实例下并发的 page 数
BODY_POLL_INTERVAL = 1.0  # 轮询正文是否已渲染出来的间隔（秒）
INSTALL_TIMEOUT = 900  # 冷环境下载浏览器可能数分钟

# gb18030 是 gb2312/gbk 的严格超集，统一收敛过去以覆盖生僻字
_ENCODING_ALIASES = {"gb2312": "gb18030", "gbk": "gb18030"}
_FALLBACK_ENCODINGS = ("utf-8", "gb18030", "latin-1")

_CHARSET_RE = re.compile(rb"""charset=["']?\s*([\w-]+)""", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{3,}")
# $_ts / __jsluid 分别是瑞数与加速乐的 JS 挑战特征，这类站点可能以 200 返回挑战页；
# 「访问异常」是 360 的 IP 层拦截页（HTTP 200、体积正常，仅标题可辨）
_CHALLENGE_RE = re.compile(
    r"Just a moment|Attention Required|请稍候|安全验证|访问异常|verify you are human|\$_ts|__jsluid",
    re.I,
)
# 浏览器把 PDF/附件当下载而非导航，goto 会直接抛这个——改用 APIRequestContext 取字节
_DOWNLOAD_RE = re.compile(r"Download is starting|ERR_ABORTED", re.I)

_SKIP_TAGS = frozenset({"script", "style", "nav", "footer", "header", "noscript"})


class ContentTooLarge(Exception):
    def __init__(self, size_bytes: int) -> None:
        self.size_bytes = size_bytes
        super().__init__(f"内容体积 {size_bytes / 1024 / 1024:.1f}MB 超过上限")


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


# ── 编码与 HTML 清洗 ────────────────────────────────────────────────────────

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


# ── 引擎可用性探测 ──────────────────────────────────────────────────────────

# 优先复用系统 Chrome，省去下载浏览器二进制；没有就用自带的 headless shell
_LAUNCH_ARGS = (
    {"headless": True, "channel": "chrome", "args": CHROME_ARGS},
    {"headless": True, "args": CHROME_ARGS},
)


def _launch_chromium(p):
    last: Exception | None = None
    for kwargs in _LAUNCH_ARGS:
        try:
            return p.chromium.launch(**kwargs)
        except Exception as e:
            last = e
    raise RuntimeError(f"chromium 启动失败: {last}")


async def _launch_chromium_async(p):
    last: Exception | None = None
    for kwargs in _LAUNCH_ARGS:
        try:
            return await p.chromium.launch(**kwargs)
        except Exception as e:
            last = e
    raise RuntimeError(f"chromium 启动失败: {last}")


@functools.lru_cache(maxsize=1)
def probe_engines() -> dict:
    """实测各层是否真正可用（而非仅猜测依赖是否安装），结果按进程缓存一次。"""
    avail = {"http": False, "browser": False, "chromium": False}

    try:
        import curl_cffi  # noqa: F401
        avail["http"] = True
    except ImportError:
        pass

    try:
        from playwright.sync_api import sync_playwright
        avail["browser"] = True
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


# ── 浏览器按需安装：仅在 http 层不过关时触发 ──────────────────────────────────

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


def _ensure_browser() -> bool:
    """确保 playwright 与浏览器可用，必要时安装。一个进程只尝试一次。"""
    global _install_attempted

    avail = probe_engines()
    if avail["browser"] and avail["chromium"]:
        return True
    if _install_attempted or _env_on("WEB_FETCH_NO_AUTO_INSTALL"):
        return False
    _install_attempted = True

    if not avail["browser"]:
        print("http 层未过关，安装 playwright...", file=sys.stderr)
        if not _install_playwright_package():
            print("  手动安装：uv pip install playwright && playwright install chromium --only-shell",
                  file=sys.stderr)
            return False
        # 包缺失时探不到浏览器，装完必须重新探一次，否则会把已缓存的浏览器又下一遍
        probe_engines.cache_clear()
        if probe_engines()["chromium"]:
            return True

    # 全程 headless，完整 chromium（约 344MB）一次都用不到，只装 headless shell
    print("下载 chromium headless shell...", file=sys.stderr)
    if not _run([sys.executable, "-m", "playwright", "install", "chromium", "--only-shell"]):
        print("  手动安装：playwright install chromium --only-shell", file=sys.stderr)
        return False

    probe_engines.cache_clear()
    avail = probe_engines()
    return avail["browser"] and avail["chromium"]


# ── http 层 ────────────────────────────────────────────────────────────────

def _fetch_http(url: str, timeout: int) -> tuple[bytes, str, int]:
    from curl_cffi import requests as cffi_requests

    r = cffi_requests.get(url, headers=HEADERS, impersonate="chrome", timeout=timeout, allow_redirects=True)
    content_length = r.headers.get("Content-Length")
    if content_length and int(content_length) > MAX_BYTES:
        raise ContentTooLarge(int(content_length))
    data = _decompress(r.content, r.headers.get("Content-Encoding", ""))
    return data, r.headers.get("Content-Type", ""), r.status_code


# ── browser 层 ─────────────────────────────────────────────────────────────

async def _wait_for_body(page, timeout: int) -> bool:
    """等到页面渲染出实质正文为止。

    正向判据：必须看见 MIN_TEXT_CHARS 以上的正文才算过关。瑞数首跳返回 412 + 混淆脚本，
    脚本执行后写 cookie 再自行重载；重载期间取内容会抛异常（不等于已通过），而挑战失败时
    页面会被清空——此时"挑战特征"同样不见了，所以绝不能用特征消失来反向判定通过。
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            text = await page.inner_text("body")
            content = await page.content()
        except Exception:
            await asyncio.sleep(BODY_POLL_INTERVAL)  # 页面正在重载
            continue

        if not _CHALLENGE_RE.search(content[:4096]) and len(text.strip()) >= MIN_TEXT_CHARS:
            return True
        await asyncio.sleep(BODY_POLL_INTERVAL)
    return False


async def _browser_fetch_one(ctx, url: str, timeout: int) -> tuple[bytes, str, int]:
    page = await ctx.new_page()
    try:
        try:
            response = await page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
        except Exception as e:
            if not _DOWNLOAD_RE.search(str(e)):
                raise
            # 浏览器把 PDF/附件当下载处理，导航必然失败；改用共享 cookie 的请求上下文取字节
            return await _request_bytes(ctx, url, timeout)

        status = response.status if response else 0
        content_type = response.headers.get("content-type", "") if response else ""

        if content_type and "html" not in content_type.lower():
            return await _request_bytes(ctx, url, timeout)

        passed = await _wait_for_body(page, timeout)
        data = (await page.content()).encode("utf-8")
        # 正文渲染出来了，说明挑战确已通过，首跳的 4xx 不代表最终结果；没渲染出来则如实
        # 保留原状态码，交给 _blocked_reason 判定——绝不把空壳洗白成 200
        return data, content_type or "text/html; charset=utf-8", 200 if passed else status
    finally:
        await page.close()


async def _request_bytes(ctx, url: str, timeout: int) -> tuple[bytes, str, int]:
    """用浏览器上下文的请求 API 取字节：共享已通过挑战的 cookie，且不触发下载行为。"""
    response = await ctx.request.get(url, timeout=timeout * 1000)
    body = await response.body()
    if len(body) > MAX_BYTES:
        raise ContentTooLarge(len(body))
    return body, response.headers.get("content-type", ""), response.status


async def _browser_fetch_many(urls: list[str], timeout: int,
                              max_workers: int) -> dict[str, tuple | Exception]:
    """一个浏览器实例、一个上下文，多个 page 并发——不是每个 URL 起一个 chromium。"""
    from playwright.async_api import async_playwright

    out: dict[str, tuple | Exception] = {}
    async with async_playwright() as p:
        browser = await _launch_chromium_async(p)
        try:
            ctx = await browser.new_context(user_agent=USER_AGENT, locale="zh-CN")
            sem = asyncio.Semaphore(max_workers)

            async def one(url: str) -> None:
                async with sem:
                    try:
                        out[url] = await _browser_fetch_one(ctx, url, timeout)
                    except Exception as e:
                        out[url] = e

            await asyncio.gather(*(one(u) for u in urls))
        finally:
            await browser.close()
    return out


def _browser_fetch(urls: list[str], timeout: int, max_workers: int) -> dict[str, tuple | Exception]:
    """同步入口。asyncio.run 要求没有正在运行的事件循环，独立线程里跑最省心。"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_browser_fetch_many(urls, timeout, max_workers))).result()


# ── 是否"没抓到"的判定 ──────────────────────────────────────────────────────

def _blocked_reason(status: int, data: bytes, content_type: str,
                    expect: re.Pattern | None = None) -> tuple[str, str] | None:
    """返回 (kind, detail)，None 表示这条响应是有效的。

    expect 是调用方声明的"有效响应长什么样"的结构哨兵。仅靠 _CHALLENGE_RE 的黑名单不够——
    拦截页可能 HTTP 200、体积正常且不含任何已知特征（360 的「访问异常页面」即如此），只有
    调用方知道自己要的页面该有什么结构。哨兵须落在文档头部 4096 字节内。
    """
    if status and status >= 400:
        return "http_error", f"HTTP {status}"
    ct = (content_type or "").lower()
    if "pdf" in ct:
        return None
    if len(data) < MIN_GOOD_BYTES:
        return "empty_body", f"响应体仅 {len(data)} 字节，疑似 JS 空壳或挑战未通过"
    sample = data[:4096].decode("utf-8", errors="ignore")
    if _CHALLENGE_RE.search(sample):
        return "challenge", "命中反爬/JS 挑战页特征"
    if expect is not None and not expect.search(sample):
        return "unexpected_structure", "响应结构不符合预期（疑似拦截页或改版）"
    return None


def _exception_reason(e: Exception) -> tuple[str, str]:
    name = type(e).__name__
    detail = f"{name}: {e}"
    if isinstance(e, ContentTooLarge):
        return "too_large", str(e)
    if "Timeout" in name or "timed out" in str(e).lower():
        return "timeout", detail
    return "network", detail


def _engine_ready(eng: str, avail: dict) -> bool:
    if eng == "browser":
        return _ensure_browser()  # 链尾兜底，缺浏览器时按需安装
    return avail["http"]


# ── 统一入口 ───────────────────────────────────────────────────────────────

def _success(url: str, eng: str, payload: tuple, attempts: list[dict]) -> dict:
    data, content_type, status = payload
    return {"url": url, "data": data, "content_type": content_type, "status": status,
            "engine_used": eng, "attempts": attempts}


def _failed(url: str, attempts: list[dict]) -> dict:
    detail = "；".join(f"{a['engine']} {a['detail']}" for a in attempts) or "没有可用引擎"
    return {"url": url, "data": None, "error": f"各层引擎均未拿到有效内容（{detail}）",
            "attempts": attempts, "engines_available": probe_engines()}


def fetch_bytes(url: str, engine: str = "auto", timeout: int = 20,
                expect: re.Pattern | None = None) -> dict:
    """抓取单个 URL 的原始字节，http 不过关就升级到 browser。

    成功 {"data": bytes, "content_type", "status", "engine_used", "attempts": [...]}
    失败 {"data": None, "error", "attempts": [{engine, kind, detail}], "engines_available"}
    """
    return fetch_many_bytes([url], engine=engine, timeout=timeout, expect=expect)[0]


def fetch_many_bytes(urls: list[str], engine: str = "auto", timeout: int = 20,
                     expect: re.Pattern | None = None,
                     max_workers: int = DEFAULT_CONCURRENCY) -> list[dict]:
    """批量抓取字节。http 层并发试完，剩下的一起交给同一个浏览器实例——避免每个 URL 起一个。"""
    chain = [engine] if engine != "auto" else list(ENGINE_NAMES)
    avail = probe_engines()
    attempts: dict[str, list[dict]] = {u: [] for u in urls}
    done: dict[str, dict] = {}
    pending = list(urls)

    for eng in chain:
        if not pending or not _engine_ready(eng, avail):
            continue

        if eng == "http":
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_fetch_http, u, timeout): u for u in pending}
                outcomes = {}
                for future in concurrent.futures.as_completed(futures):
                    url = futures[future]
                    try:
                        outcomes[url] = future.result()
                    except Exception as e:  # noqa: BLE001 - 逐条记录失败原因，不中断其余 URL
                        outcomes[url] = e
        else:
            outcomes = _browser_fetch(pending, max(timeout, BROWSER_MIN_TIMEOUT), max_workers)

        still_pending = []
        for url in pending:
            outcome = outcomes.get(url)
            if isinstance(outcome, Exception):
                kind, detail = _exception_reason(outcome)
                attempts[url].append({"engine": eng, "kind": kind, "detail": detail})
                still_pending.append(url)
                continue

            data, content_type, status = outcome
            reason = _blocked_reason(status, data, content_type, expect)
            if reason is None:
                done[url] = _success(url, eng, outcome, attempts[url])
                continue
            kind, detail = reason
            attempts[url].append({"engine": eng, "kind": kind, "detail": detail})
            still_pending.append(url)

        pending = still_pending

    for url in pending:
        done[url] = _failed(url, attempts[url])
    return [done[u] for u in urls]


# ── PDF 文本抽取 ────────────────────────────────────────────────────────────

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


# ── 抓取 + 清洗 ────────────────────────────────────────────────────────────

def _failure(fetched: dict) -> dict:
    return {"url": fetched["url"], "error": fetched["error"],
            "attempts": fetched.get("attempts", []),
            "engines_available": fetched.get("engines_available", {})}


def _clean_one(fetched: dict, max_chars: int, max_pages: int) -> dict:
    if fetched.get("data") is None:
        return _failure(fetched)

    url = fetched["url"]
    data = fetched["data"]
    content_type = fetched.get("content_type") or ""
    engine_used = fetched["engine_used"]
    degraded = engine_used != "http"

    if "pdf" in content_type.lower():
        pdf_result = _extract_pdf(data, max_chars, max_pages)
        pdf_result.update({"url": url, "engine_used": engine_used, "degraded": degraded})
        return pdf_result

    html_text = _decode_text(data, content_type)
    text = clean_html(html_text)
    # 抓到一具空壳（挑战未通过、JS 未渲染）时宁可报错，绝不返回只剩标题的"成功"让调用方拿去分析
    if len(text) < MIN_TEXT_CHARS:
        return {
            "url": url,
            "error": f"正文仅 {len(text)} 字符（低于 {MIN_TEXT_CHARS}），疑似挑战未通过或空壳页面",
            "engine_used": engine_used,
            "attempts": fetched.get("attempts", []),
        }

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


def _raw_one(fetched: dict) -> dict:
    if fetched.get("data") is None:
        return _failure(fetched)

    url = fetched["url"]
    content_type = fetched.get("content_type") or ""
    if "pdf" in content_type.lower():
        return {"url": url, "error": "raw 模式不支持 PDF，请改用默认模式抽取文本",
                "attempts": fetched.get("attempts", [])}

    raw = _decode_text(fetched["data"], content_type)
    engine_used = fetched["engine_used"]
    return {
        "url": url,
        "engine_used": engine_used,
        "status": fetched.get("status", 0),
        "content_type": content_type,
        "degraded": engine_used != "http",
        "length": len(raw),
        "raw": raw,
    }


def fetch_one(url: str, max_chars: int = 8000, max_pages: int = 30, engine: str = "auto",
              timeout: int = 20) -> dict:
    return _clean_one(fetch_bytes(url, engine=engine, timeout=timeout), max_chars, max_pages)


def fetch_raw(url: str, engine: str = "auto", timeout: int = 20) -> dict:
    """抓取并解码原始响应体，不做正文清洗也不截断。

    供需要自行解析结构的调用方使用（JSON 接口、需要正则提取 href 的列表页），清洗与截断
    都会破坏这类解析。二进制内容（PDF）不适用，请改用 fetch_one。
    """
    return _raw_one(fetch_bytes(url, engine=engine, timeout=timeout))


def fetch_many(urls: list[str], max_chars: int = 8000, max_pages: int = 30, engine: str = "auto",
               timeout: int = 20, raw: bool = False,
               max_workers: int = DEFAULT_CONCURRENCY) -> list[dict]:
    """批量抓取 + 清洗。需要浏览器的 URL 共用同一个浏览器实例。"""
    fetched = fetch_many_bytes(urls, engine=engine, timeout=timeout, max_workers=max_workers)
    if raw:
        return [_raw_one(f) for f in fetched]
    return [_clean_one(f, max_chars, max_pages) for f in fetched]
