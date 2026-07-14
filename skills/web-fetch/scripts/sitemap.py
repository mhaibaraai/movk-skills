#!/usr/bin/env python3
# /// script
# dependencies = ["pypdf>=4.0", "curl_cffi>=0.7"]
# ///
"""
sitemap 发现层：域名 [+路径过滤 +时间范围] -> 候选 URL。零 API Key，直连原站。

与 search.py 的分工：搜索引擎擅长模糊关键词匹配，但覆盖率取决于它索引了什么——
实测 360 对 site:iea.org 只返回 1 条首页，而 IEA 自己的 sitemap 里有 2926 条报告，
每条还带 <lastmod> 日期。所以"枚举某站某类内容"「取最新一期」「筛某时间段」这类需求
应当首选本模块：覆盖全、带日期、一个 HTTP 请求拿到，不必启动浏览器。

站点没有 sitemap、或 sitemap 被反爬挡住时，才回落到 search.py。

发现顺序：/robots.txt 的 Sitemap: 指令（可能多条）-> 回落 /sitemap.xml。
<sitemapindex> 会递归展开（受 MAX_INDEX_FETCH 限制），.xml.gz 按 magic number 解压。

CLI:
  uv run scripts/sitemap.py --site iea.org --match /reports/ --since 2025-01-01
  uv run scripts/sitemap.py --site shell.com --match sustainability --max-results 20

输出 JSON: {"results": [...], "errors": [...]}   —— 与 search.py 同构，便于调用方统一处理
  results[] 含 url, lastmod（无则为 ""）, rank
  errors[]  含 kind, detail
            kind 取值 no_match（sitemap 有效但无条目命中过滤）、no_sitemap（站点没有
                      sitemap）、network_unreachable、http_error、blocked（各层引擎
                      均未拿到 sitemap）
"""
import argparse
import gzip
import json
import re
import sys
import urllib.parse

from engines import ENGINE_NAMES, fetch_bytes

MAX_INDEX_FETCH = 20  # sitemapindex 最多展开的子 sitemap 数，避免大站把沙箱拖垮

# sitemap 是静态 XML，浏览器层对它天然无用且有害：Chromium 会把 XML 渲染成自己的树视图
# DOM，毁掉 <loc> 解析，还会为了一个 404 探测触发浏览器下载。所以本模块固定走 http 层。
SITEMAP_ENGINE = "http"

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_ENTRY_RE = re.compile(r"<(url|sitemap)\b.*?</\1>", re.I | re.S)
_LASTMOD_RE = re.compile(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", re.I)
_ROBOTS_SITEMAP_RE = re.compile(r"^\s*sitemap\s*:\s*(\S+)", re.I | re.M)
# sitemap 是 XML，不是 HTML —— 用它当哨兵，防止把反爬页/软 404 的 HTML 当成空 sitemap
_XML_RE = re.compile(r"<(?:urlset|sitemapindex)\b", re.I)


def _decompress_gz(data: bytes) -> bytes:
    """.xml.gz 形式的 sitemap 很常见。engines 只处理 Content-Encoding 头，
    响应体本身是 gzip 文件时要在这里按 magic number 解。"""
    if data[:2] != b"\x1f\x8b":
        return data
    try:
        return gzip.decompress(data)
    except OSError:
        return data


# engines 的逐层失败 kind -> 本模块对外的 errors[].kind
_KIND_MAP = {
    "challenge": "blocked",
    "empty_body": "blocked",
    "unexpected_structure": "blocked",
    "timeout": "network_unreachable",
    "network": "network_unreachable",
    "http_error": "http_error",
    "too_large": "invalid_response",
}


def _fetch_xml(url: str, engine: str) -> tuple[str, dict | None]:
    """取一份 sitemap/robots，返回 (文本, 错误)。错误为 None 表示成功。"""
    fetched = fetch_bytes(url, engine=engine)
    if fetched.get("data") is not None:
        return _decompress_gz(fetched["data"]).decode("utf-8", errors="replace"), None

    attempts = fetched.get("attempts") or []
    detail = fetched.get("error", "")
    kind = _KIND_MAP.get(attempts[-1]["kind"], "invalid_response") if attempts else "invalid_response"
    return "", {"kind": kind, "detail": detail}


def _roots(site: str) -> list[str]:
    """站点根 URL 候选。裸顶级域未必对外服务（如 ndrc.gov.cn 连不上、www.ndrc.gov.cn 才是本体），
    所以顶级域要补一个 www. 变体。"""
    base = site if "://" in site else f"https://{site}"
    parsed = urllib.parse.urlsplit(base)
    scheme = parsed.scheme or "https"
    host = parsed.netloc or parsed.path

    roots = [f"{scheme}://{host}"]
    if host.count(".") == 1:  # 顶级域（example.com），补 www
        roots.append(f"{scheme}://www.{host}")
    return roots


def discover(site: str, engine: str) -> tuple[list[str], dict | None]:
    """定位站点的 sitemap 入口：robots.txt 的 Sitemap: 指令优先，回落 /sitemap.xml。

    探测失败（404/连不上）就是"没有 sitemap"这个确定结论——静态文件不存在，换引擎也变不出来，
    调用方据此回落到 search.py 即可。
    """
    blocked: dict | None = None

    for root in _roots(site):
        robots, _ = _fetch_xml(f"{root}/robots.txt", engine)
        declared = _ROBOTS_SITEMAP_RE.findall(robots) if robots else []
        if declared:
            return declared, None

        fallback = f"{root}/sitemap.xml"
        text, error = _fetch_xml(fallback, engine)
        if error:
            if error["kind"] == "blocked":
                blocked = error  # 反爬拦截和"不存在"是两回事，要区别告知
            continue
        if _XML_RE.search(text):
            return [fallback], None

    if blocked:
        return [], blocked
    return [], {"kind": "no_sitemap",
                "detail": f"{site} 未在 robots.txt 声明 sitemap，/sitemap.xml 也不存在或不是有效 sitemap；请改用 search.py"}


def _parse(text: str) -> tuple[list[dict], list[str]]:
    """解析一份 sitemap，返回 (条目, 子 sitemap 链接)。

    <sitemapindex> 里的 <loc> 是子 sitemap，<urlset> 里的才是内容 URL——两者标签同名，
    只能按外层容器区分，所以逐条 <url>/<sitemap> 块地解，顺便把 lastmod 绑到对的条目上。
    """
    is_index = bool(re.search(r"<sitemapindex\b", text, re.I))
    entries, children = [], []
    for block in _ENTRY_RE.finditer(text):
        chunk = block.group(0)
        loc = _LOC_RE.search(chunk)
        if not loc:
            continue
        url = loc.group(1).strip()
        if block.group(1).lower() == "sitemap" or is_index:
            children.append(url)
        else:
            lastmod = _LASTMOD_RE.search(chunk)
            entries.append({"url": url, "lastmod": lastmod.group(1).strip() if lastmod else ""})
    return entries, children


def collect(site: str, match: str, since: str, max_results: int, engine: str) -> dict:
    roots, error = discover(site, engine)
    if error:
        return {"results": [], "errors": [error]}

    pattern = re.compile(match, re.I) if match else None
    pending, seen, entries, errors = list(roots), set(), [], []
    fetched_count = 0

    while pending and fetched_count < MAX_INDEX_FETCH:
        url = pending.pop(0)
        if url in seen:
            continue
        seen.add(url)
        fetched_count += 1

        text, err = _fetch_xml(url, engine)
        if err:
            errors.append(err)
            continue
        if not _XML_RE.search(text):
            errors.append({"kind": "invalid_response", "detail": f"{url} 不是有效 sitemap（可能是反爬页或软 404）"})
            continue

        found, children = _parse(text)
        entries.extend(found)
        pending.extend(c for c in children if c not in seen)

    hits = [
        e for e in entries
        if (not pattern or pattern.search(e["url"])) and (not since or (e["lastmod"] and e["lastmod"][:10] >= since))
    ]
    # lastmod 降序，最新在前；无日期的条目沉底
    hits.sort(key=lambda e: e["lastmod"] or "", reverse=True)

    results = [{**e, "rank": i + 1} for i, e in enumerate(hits[:max_results])]
    if not results and not errors:
        scope = f"（共扫描 {len(entries)} 条 sitemap 条目）"
        errors.append({"kind": "no_match", "detail": f"sitemap 有效但无条目命中过滤条件{scope}"})
    return {"results": results, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="sitemap 发现层，零 API Key，直连原站")
    parser.add_argument("--site", "-s", required=True, help="站点域名，如 iea.org")
    parser.add_argument("--match", "-m", default="", help="URL 过滤正则，如 /reports/")
    parser.add_argument("--since", default="", help="按 lastmod 筛起始日期，格式 YYYY-MM-DD")
    parser.add_argument("--max-results", "-n", type=int, default=20)
    parser.add_argument("--engine", choices=["auto", *ENGINE_NAMES], default=SITEMAP_ENGINE,
                        help="默认固定 http：静态 XML 经浏览器渲染会被毁掉（见源码注释）")
    args = parser.parse_args()

    print(f"sitemap: {args.site} match={args.match or '(不限)'} since={args.since or '(不限)'}...", file=sys.stderr)
    payload = collect(args.site, args.match, args.since, args.max_results, args.engine)
    print(f"{len(payload['results'])} 条结果，{len(payload['errors'])} 条错误", file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
