#!/usr/bin/env python3
# /// script
# dependencies = ["pypdf>=4.0", "curl_cffi>=0.7"]
# ///
"""
单站全链路诊断：一条命令跑完发现与抓取的每一层，输出一份可直接粘回的报告。

与 fetch.py --check-env 的分工：--check-env 只答「引擎装没装」，本脚本答「这个站在
这个环境里到底卡在哪一层、卡的是什么」。抓取成败高度依赖部署环境——瑞数挑战能否过关
取决于浏览器能否真启动，360 的拦截是 IP 层的——所以任何结论都必须在目标环境里取，
本机跑出来的不算数。

关键设计：绕开判定层，直取原始事实。
  fetch_bytes 的降级链会把失败压成 {kind, detail}，丢掉了诊断最需要的东西（响应字节数、
  页面标题、耗时、命中的是哪条挑战特征）。本脚本改为直接调 _fetch_http / _browser_fetch
  逐层单跑单计时拿原始响应，再单独调 _blocked_reason 报告「判定层会怎么判」。报告里因此
  同时有两行：原始事实与判定结论。二者不一致，缺陷在判定层；一致，缺陷在别处。

七个步骤：
  1 engines        那边有没有浏览器、chromium 能不能真启动
  2 sitemap        no_sitemap 是真结论还是被反爬误判（robots.txt 与 /sitemap.xml 原始响应）
  3 serp           360 检索被拦在哪一层，出口 IP 是否被拉黑
  4 entry_http     入口页首跳状态码、是否命中 $_ts 一类挑战特征
  5 entry_browser  浏览器能否越过挑战、耗时多少、渲染出多少正文
  6 links          页内附件与同站链接，带锚文本（见下）
  7 target         按 --match 命中的那条附件抓一次，端到端确认能拿到全文

步骤 6 是修复方案的预演：extract_attachments 现在只给 {url, ext}，而同一个页面里十余条
PDF 从 URL 上分不出哪条是要的（拼音缩写路径 qyshzrbg 只能靠猜），技能却又明令禁止猜 URL。
锚文本是唯一的判别依据。这里先原型实现一遍，在目标环境验证成立后再搬进 engines.py。

CLI:
  uv run scripts/diagnose.py --site cnpc.com.cn --query "2025 社会责任报告" --match "社会责任报告"
  uv run scripts/diagnose.py --site iea.org --skip-browser

进度与人读报告走 stderr、JSON 走 stdout：用管道解析 JSON 时不要 2>&1。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse

import engines
import search
import sitemap

MAX_LIST = 30  # 附件/链接列表的输出上限，避免报告长到没法读
PDF_HEAD_CHARS = 200  # 步骤 7 里回显的正文首段长度


def _entry_candidates(site: str, explicit: str) -> list[str]:
    """入口页 URL 候选。裸多级域名常不直接服务（cnpc.com.cn 裸域连不上、www.cnpc.com.cn 才是
    本体），所以默认优先 www.，再回落裸域。子域名站点（news.cnpc.com.cn）请用 --url 显式指定。"""
    if explicit:
        return [explicit]
    urls = []
    if not site.startswith("www."):
        urls.append(f"https://www.{site}/")
    urls.append(f"https://{site}/")
    return list(dict.fromkeys(urls))


def _describe(data: bytes, content_type: str, status: int, url: str,
              expect: re.Pattern | None = None) -> dict:
    """把一条原始响应摊开成「事实 + 判定」两部分。"""
    title, text_len = "", 0
    if not engines._is_pdf(data, content_type):
        html_text = engines._decode_text(data, content_type)
        title = engines.extract_title(html_text)
        text_len = len(engines.clean_html(html_text))

    hit = engines._CHALLENGE_RE.search(data[:4096].decode("utf-8", errors="ignore"))
    reason = engines._blocked_reason(status, data, content_type, expect, url)
    return {
        "status": status,
        "bytes": len(data),
        "content_type": content_type,
        "title": title,
        "text_len": text_len,
        "challenge_hit": hit.group(0) if hit else "",
        "verdict": "pass" if reason is None else reason[0],
        "verdict_detail": "" if reason is None else reason[1],
    }


def _http(url: str, timeout: int = 20) -> tuple[bytes, str, int, float]:
    started = time.perf_counter()
    data, content_type, status = engines._fetch_http(url, timeout)
    return data, content_type, status, round(time.perf_counter() - started, 1)


def _browser(url: str) -> tuple[bytes, str, int, float]:
    started = time.perf_counter()
    outcome = engines._browser_fetch([url], engines.BROWSER_MIN_TIMEOUT, 1)[url]
    elapsed = round(time.perf_counter() - started, 1)
    if isinstance(outcome, Exception):
        raise outcome
    data, content_type, status = outcome
    return data, content_type, status, elapsed


def _probe(url: str, engine: str, expect: re.Pattern | None = None) -> dict:
    """跑一层引擎并摊开结果。异常也如实记录，不让单格失败中断整份报告。"""
    try:
        data, content_type, status, elapsed = _http(url) if engine == "http" else _browser(url)
    except Exception as e:  # noqa: BLE001 - 诊断脚本的职责就是把异常原样报出来
        return {"engine": engine, "url": url, "error": f"{type(e).__name__}: {e}"}
    row = _describe(data, content_type, status, url, expect)
    return {"engine": engine, "url": url, "elapsed": elapsed, **row, "_data": data,
            "_content_type": content_type}


def _public(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _redirect(url: str) -> dict:
    """首跳是否 3xx，跳去哪。_fetch_http 会自动跟随重定向，那会掩盖掉「跳去了错误页/验证页」
    这个关键事实——sitemap.xml 跳错误页、SERP 跳验证页都只在这一步才看得见。"""
    try:
        from curl_cffi import requests as cffi_requests
        r = cffi_requests.get(url, headers=engines.HEADERS, impersonate="chrome",
                              timeout=15, allow_redirects=False)
        return {"status": r.status_code, "location": r.headers.get("Location", "")}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def step_engines() -> dict:
    engines.probe_engines.cache_clear()
    return dict(engines.probe_engines())


def step_sitemap(site: str) -> dict:
    """复现 sitemap.py 的发现路径，但把每一跳的原始响应摊开。"""
    out = {"roots": []}
    for root in sitemap._roots(site):
        entry: dict = {"root": root}

        robots = _probe(f"{root}/robots.txt", "http")
        if "error" not in robots:
            text = engines._decode_text(robots["_data"], robots["_content_type"])
            entry["sitemap_declared"] = sitemap._ROBOTS_SITEMAP_RE.findall(text)
        entry["robots"] = _public(robots)

        target = f"{root}/sitemap.xml"
        entry["sitemap_redirect"] = _redirect(target)
        probe = _probe(target, "http")
        if "error" not in probe:
            text = engines._decode_text(probe["_data"], probe["_content_type"])
            entry["is_xml"] = bool(sitemap._XML_RE.search(text))
        entry["sitemap_xml"] = _public(probe)

        out["roots"].append(entry)
    return out


def step_serp(site: str, query: str, skip_browser: bool) -> dict:
    q = f"site:{site} {query}".strip()
    url = f"{search.SEARCH_URL}?{urllib.parse.urlencode({'q': q})}"
    out: dict = {"query": q, "url": url, "redirect": _redirect(url), "layers": []}

    for engine in ("http",) if skip_browser else ("http", "browser"):
        probe = _probe(url, engine, expect=search._SERP_RE)
        if "error" not in probe:
            text = engines._decode_text(probe["_data"], probe["_content_type"])
            probe["result_blocks"] = len(search._RESULT_RE.findall(text))
        out["layers"].append(_public(probe))
    return out


def step_entry(candidates: list[str], engine: str) -> dict:
    """逐个候选入口试，第一个非异常的结果胜出；全失败则返回最后一个的异常。"""
    last = None
    for url in candidates:
        probe = _probe(url, engine)
        if "error" not in probe:
            return probe
        last = probe
    return last or {"engine": engine, "error": "无候选入口"}


def step_links(entry: dict) -> dict:
    if "error" in entry or engines._is_pdf(entry["_data"], entry["_content_type"]):
        return {"error": "入口页未抓到 HTML，无法提取链接"}
    html_text = engines._decode_text(entry["_data"], entry["_content_type"])
    attachments, pages = engines.extract_links(html_text, entry["url"])
    return {
        "source_engine": entry["engine"],
        "attachments_total": len(attachments),
        "pages_total": len(pages),
        "attachments": attachments[:MAX_LIST],
        "pages": pages[:MAX_LIST],
    }


def step_target(links: dict, match: str) -> dict:
    if not match:
        return {"skipped": "未提供 --match"}
    if "error" in links:
        return {"skipped": "入口页无链接可筛"}

    pattern = re.compile(match, re.I)
    hits = [a for a in links.get("attachments", []) if pattern.search(a["text"])]
    if not hits:
        return {"matched": 0,
                "detail": f"没有附件的锚文本命中 {match!r}——这本身就是结论：锚文本不足以定位目标"}

    target = hits[0]
    probe = _probe(target["url"], "http")
    if "error" in probe:
        probe = _probe(target["url"], "browser")
    if "error" in probe:
        return {"matched": len(hits), "target": target, "fetch": probe}

    out = {"matched": len(hits), "target": target,
           "fetch": {k: v for k, v in _public(probe).items() if k != "title"}}
    if engines._is_pdf(probe["_data"], probe["_content_type"]):
        pdf = engines._extract_pdf(probe["_data"], PDF_HEAD_CHARS, max_pages=2)
        out["pdf"] = {"pages": pdf.get("pages"), "title": pdf.get("title"),
                      "head": (pdf.get("text") or "")[:PDF_HEAD_CHARS],
                      "error": pdf.get("error", "")}
    else:
        out["pdf"] = {"error": "请求的附件返回的不是 PDF"}
    return out


def _row(label: str, row: dict) -> str:
    if "error" in row:
        return f"  {label:<10} 异常 {row['error']}"
    parts = [f"HTTP {row.get('status')}", f"{row.get('bytes')}B", f"{row.get('elapsed')}s",
             f"正文 {row.get('text_len')}字"]
    if row.get("result_blocks") is not None:
        parts.append(f"结果块 {row['result_blocks']}")
    if row.get("challenge_hit"):
        parts.append(f"挑战特征 {row['challenge_hit']!r}")
    if row.get("title"):
        parts.append(f"title={row['title'][:30]!r}")
    parts.append(f"→ 判定 {row.get('verdict')}")
    return f"  {label:<10} " + "  ".join(parts)


def render(report: dict) -> None:
    """人读报告，走 stderr。"""
    s = report["steps"]
    out = sys.stderr

    print(f"\n站点 {report['site']}  入口 {report['entry_used'] or report['entry_candidates']}\n", file=out)

    print("1. 引擎探测", file=out)
    print(f"  {s['engines']}", file=out)

    print("\n2. sitemap 链路", file=out)
    for root in s["sitemap"]["roots"]:
        print(f"  [{root['root']}]", file=out)
        print(_row("robots", root["robots"]), file=out)
        print(f"    Sitemap: 声明 = {root.get('sitemap_declared') or '无'}", file=out)
        redirect = root["sitemap_redirect"]
        print(f"    /sitemap.xml 首跳 = {redirect.get('status')} "
              f"{redirect.get('location') or redirect.get('error') or ''}", file=out)
        print(_row("sitemap", root["sitemap_xml"]), file=out)
        print(f"    是有效 XML = {root.get('is_xml')}", file=out)

    print(f"\n3. 360 SERP（q={s['serp']['query']!r}）", file=out)
    redirect = s["serp"]["redirect"]
    print(f"  首跳 = {redirect.get('status')} {redirect.get('location') or redirect.get('error') or ''}",
          file=out)
    for layer in s["serp"]["layers"]:
        print(_row(layer["engine"], layer), file=out)

    print("\n4/5. 入口页抓取", file=out)
    print(_row("http", s["entry_http"]), file=out)
    print(_row("browser", s["entry_browser"]), file=out)

    print("\n6. 页内链接（带锚文本）", file=out)
    links = s["links"]
    if "error" in links:
        print(f"  {links['error']}", file=out)
    else:
        print(f"  来源引擎 {links['source_engine']}  附件 {links['attachments_total']} 条，"
              f"同站链接 {links['pages_total']} 条（各显示前 {MAX_LIST}）", file=out)
        for a in links["attachments"]:
            print(f"    [{a['ext']}] {a['text'][:38]!r:42} {a['url'][-58:]}", file=out)
        for p in links["pages"][:10]:
            print(f"    [页面] {p['text'][:38]!r:42} {p['url'][-58:]}", file=out)

    print("\n7. 目标附件", file=out)
    target = s["target"]
    if "skipped" in target:
        print(f"  跳过：{target['skipped']}", file=out)
    elif not target.get("matched"):
        print(f"  {target.get('detail')}", file=out)
    else:
        print(f"  锚文本命中 {target['matched']} 条，取第一条：{target['target']['text']!r}", file=out)
        print(f"    {target['target']['url']}", file=out)
        print(_row("fetch", target["fetch"]), file=out)
        pdf = target.get("pdf", {})
        print(f"    PDF {pdf.get('pages')} 页  title={pdf.get('title')!r}", file=out)
        if pdf.get("head"):
            print(f"    首段：{pdf['head'][:120]!r}", file=out)
        if pdf.get("error"):
            print(f"    PDF 异常：{pdf['error']}", file=out)
    print(file=out)


def main() -> None:
    parser = argparse.ArgumentParser(description="单站全链路诊断，输出可粘回的环境报告")
    parser.add_argument("--site", "-s", required=True, help="站点域名，如 cnpc.com.cn")
    parser.add_argument("--url", "-u", default="", help="入口页，默认 https://www.<site>/")
    parser.add_argument("--query", "-q", default="", help="360 检索测试词，与 site: 拼接")
    parser.add_argument("--match", "-m", default="",
                        help="按锚文本筛目标附件的正则，如「社会责任报告」")
    parser.add_argument("--skip-browser", action="store_true", help="只跑 http 层，快速排查")
    args = parser.parse_args()

    site = args.site.strip().removeprefix("https://").removeprefix("http://").strip("/")
    candidates = _entry_candidates(site, args.url)

    print(f"诊断 {site}（入口候选 {candidates}）...", file=sys.stderr)
    steps: dict = {}

    # 浏览器先一次性备好：步骤 3 的 SERP browser 探测与步骤 5 都要用，不然首跑必然
    # ModuleNotFoundError（安装发生在按需时机，太晚）。引擎探测放在安装之后再做，
    # 否则报告里的"引擎探测"永远是安装前的假状态（browser: false），会让人误判
    # 目标环境没有浏览器——探测要反映实际能力，不是首次探测时的空白状态。
    browser_ready = False if args.skip_browser else engines._ensure_browser()

    steps["engines"] = step_engines()
    print("  [1/7] 引擎探测完成", file=sys.stderr)

    steps["sitemap"] = step_sitemap(site)
    print("  [2/7] sitemap 链路完成", file=sys.stderr)

    steps["serp"] = step_serp(site, args.query, args.skip_browser or not browser_ready)
    print("  [3/7] 360 SERP 完成", file=sys.stderr)

    entry_http = step_entry(candidates, "http")
    steps["entry_http"] = _public(entry_http)
    print("  [4/7] 入口页 http 层完成", file=sys.stderr)

    if args.skip_browser:
        entry_browser = {"engine": "browser", "error": "已用 --skip-browser 跳过"}
    elif not browser_ready:
        entry_browser = {"engine": "browser", "error": "浏览器不可用且安装失败"}
    else:
        entry_browser = step_entry(candidates, "browser")
    steps["entry_browser"] = _public(entry_browser)
    print("  [5/7] 入口页 browser 层完成", file=sys.stderr)

    # 优先用浏览器渲染后的 DOM 提链接：http 层拿到的可能是挑战页空壳，里面没有真实链接
    source = entry_browser if "error" not in entry_browser else entry_http
    steps["links"] = step_links(source)
    print("  [6/7] 链接提取完成", file=sys.stderr)

    steps["target"] = step_target(steps["links"], args.match)
    print("  [7/7] 目标附件完成", file=sys.stderr)

    report = {"site": site, "entry_candidates": candidates,
              "entry_used": entry_browser.get("url") or entry_http.get("url", ""), "steps": steps}
    render(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
