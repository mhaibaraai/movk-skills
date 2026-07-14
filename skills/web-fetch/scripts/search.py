#!/usr/bin/env python3
# /// script
# dependencies = ["pypdf>=4.0", "curl_cffi>=0.7"]
# ///
"""
360 搜索检索层：关键词 [+域名限定] -> 候选 URL。零 API Key。

已实测比选无 Key 搜索端点：DuckDuckGo html/lite 返回反爬挑战页（0 结果）、
Bing/cn.bing 是 JS 空壳（0 个结果块）、百度跳验证码——唯 360 www.so.com/s
可脚本化：site: 限定生效，结果块 `data-mdurl` 属性直接给出真实外链（无需解
跳转包装），中英文查询均返回真实结果，连续请求未触发验证码。

真实结果块结构（已用样本核实，导航/反馈等站内链接不在此结构内，天然被排除）：
  <h3 class="res-title ..."><a href="..." data-mdurl="真实外链" ...>标题(含 <em> 高亮)</a></h3>

底层抓取复用 engines.fetch_bytes，命中验证码/反爬时会按四层引擎自动升级。

360 会对可疑 IP 直接返回「访问异常页面」（HTTP 200、约 10KB、0 个结果块）。实测这是
IP 层拦截：补 Referer/Sec-Fetch 等请求头无效，curl_cffi 的 TLS 指纹伪装也过不去，只有
reader_proxy（从远端 IP 发起）能拿到真结果——所以本模块在常见部署环境下会常态化经
reader_proxy，查询词与目标域名会外发给渲染代理，且受其配额限制。

拦截页与真 SERP 靠 _SERP_RE 哨兵区分，不能靠"解析出 0 条结果"来判断：360 对胡乱查询
也会返回模糊结果（实测 7 条），真正的零结果几乎不出现，所以"0 条"在现实中基本等同于
"被拦截"。哨兵交给 fetch_bytes 的 expect 参数，不匹配就继续升级引擎。

CLI:
  uv run scripts/search.py --query "world energy outlook" --site iea.org
  uv run scripts/search.py --query "中国石化 年报"

输出 JSON: {"results": [...], "errors": [...]}
  results[] 含 title, url, rank
  errors[]  含 kind, detail
            kind 取值 no_match（SERP 有效但确无结果，罕见）、network_unreachable、
                      http_error、invalid_response、
                      blocked（各层引擎均未拿到有效 SERP，含 360 的 IP 层拦截）
"""
import argparse
import json
import re
import sys
import urllib.parse

from engines import ENGINE_NAMES, fetch_bytes, strip_tags

SEARCH_URL = "https://www.so.com/s"

_RESULT_RE = re.compile(
    r'<h3 class="res-title[^"]*"[^>]*>\s*<a\s+href="[^"]*"\s+data-mdurl="([^"]+)"[^>]*>(.*?)</a>\s*</h3>',
    re.S,
)
_OWN_DOMAINS = ("so.com", "360.com", "qhimg.com", "qhres.com", "haosou.com", "360kan.com", "bing.com")

# 有效 SERP 的结构哨兵：360 会把查询词回显进标题（"xxx_360搜索"），有结果与零结果均然；
# 而 IP 层拦截页标题是「访问异常页面」。没有它，拦截页会被当成"0 个结果块"误报为 no_match。
_SERP_RE = re.compile(r"_360搜索\s*</title>", re.I)


def _is_own_domain(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in _OWN_DOMAINS)


def _classify_fetch_error(fetched: dict) -> tuple[str, str]:
    """把 fetch_bytes 的失败结果归类为 errors[].kind。"""
    error = fetched.get("error", "")
    if "疑似反爬" in error or "验证" in error or "访问异常" in error:
        return "blocked", error
    if any(k in error for k in ("URLError", "TimeoutError", "ConnectionError", "OSError", "SSLError", "CertificateVerifyError")):
        return "network_unreachable", error
    if "HTTPError" in error or error.startswith("HTTP "):
        return "http_error", error
    return "invalid_response", error


def search(query: str, site: str, max_results: int, engine: str) -> dict:
    q = f"site:{site} {query}".strip() if site else query
    url = f"{SEARCH_URL}?{urllib.parse.urlencode({'q': q})}"

    fetched = fetch_bytes(url, engine=engine, expect=_SERP_RE)
    if fetched.get("data") is None:
        kind, detail = _classify_fetch_error(fetched)
        return {"results": [], "errors": [{"kind": kind, "detail": detail}]}

    html_text = fetched["data"].decode("utf-8", errors="replace")
    results = []
    for match in _RESULT_RE.finditer(html_text):
        result_url = match.group(1)
        title = strip_tags(match.group(2))
        if _is_own_domain(result_url):
            continue
        results.append({"title": title, "url": result_url, "rank": len(results) + 1})
        if len(results) >= max_results:
            break

    errors = [] if results else [{"kind": "no_match", "detail": "该查询无匹配结果"}]
    return {"results": results, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="360 搜索检索，零 API Key")
    parser.add_argument("--query", "-q", required=True, help="检索关键词")
    parser.add_argument("--site", "-s", default="", help="限定域名，如 iea.org（对应 site: 语法）")
    parser.add_argument("--max-results", "-n", type=int, default=10)
    parser.add_argument("--engine", choices=["auto", *ENGINE_NAMES], default="auto")
    args = parser.parse_args()

    print(f"检索: {args.query!r} site={args.site or '(不限)'}...", file=sys.stderr)
    payload = search(args.query, args.site, args.max_results, args.engine)
    print(f"{len(payload['results'])} 条结果，{len(payload['errors'])} 条错误", file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
