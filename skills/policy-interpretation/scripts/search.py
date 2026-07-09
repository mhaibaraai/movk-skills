#!/usr/bin/env python3
# /// script
# dependencies = ["ddgs"]
# ///
"""
统一政策检索入口，零 API Key。内部自动编排两层检索，调用方无需关心分层：

  官网直连  抓 departments.py 里核实过的服务端渲染列表页，正则提取候选文档，最快最准。
  网页检索  官网直连候选不足、或该部委未维护抓取规则时触发，ddgs 多引擎聚合，
            用 site: 限定域名保证来源权威。

CLI:
  uv run scripts/search.py --dept ndrc,miit,mem --keywords "节能减排" --max-results 5
  uv run scripts/search.py --dept mee --keywords "碳排放 交易" --timelimit m

输出 JSON: [{dept, dept_name, title, url, date, source_tier}]
source_tier 取值 official_site 或 web_search。
"""

import argparse
import concurrent.futures
import json
import re
import sys
from urllib.parse import urljoin, urlparse

from departments import ASSOCIATED_DOMAINS, DEPARTMENTS, resolve_display_name, resolve_site_domain
from fetch import fetch_raw

# 政务网站文档 URL 普遍自带 tYYYYMMDD_编号，可反推发布日期
_URL_DATE_RE = re.compile(r"t(\d{8})_\d+\.s?html")

TIMELIMIT_CHOICES = ("d", "w", "m", "y")


def _normalize_date(date_hint: str | None) -> str | None:
    if not date_hint:
        return None
    if len(date_hint) == 8:
        return f"{date_hint[0:4]}-{date_hint[4:6]}-{date_hint[6:8]}"
    if len(date_hint) == 4:
        return date_hint
    return None


def official_site_candidates(dept_code: str, keywords: list[str]) -> list[dict]:
    """抓部委官网列表页，正则提取候选文档，按关键词过滤标题。"""
    source = DEPARTMENTS.get(dept_code)
    if source is None:
        return []

    try:
        html_text = fetch_raw(source.listing_url)
    except Exception as e:
        print(f"  [{dept_code}] 官网列表页抓取失败，转网页检索: {e}", file=sys.stderr)
        return []

    candidates = []
    for href, date_hint, title in source.link_pattern.findall(html_text):
        title = title.strip()
        if keywords and not any(kw in title for kw in keywords):
            continue
        candidates.append({
            "dept": dept_code,
            "dept_name": source.name,
            "title": title,
            "url": urljoin(source.listing_url, href),
            "date": _normalize_date(date_hint),
            "source_tier": "official_site",
        })
    return candidates


def web_search_candidates(dept_code: str, keywords: list[str], max_results: int, timelimit: str) -> list[dict]:
    """ddgs 多引擎兜底检索，用 site: 限定域名保证来源权威。"""
    from ddgs import DDGS

    domain = resolve_site_domain(dept_code)
    query = " ".join(keywords)
    if domain:
        query = f"{query} site:{domain}"

    try:
        results = DDGS().text(query, backend="auto", timelimit=timelimit, max_results=max_results)
    except Exception as e:
        print(f"  [{dept_code}] 网页检索失败: {e}", file=sys.stderr)
        return []

    candidates = []
    for r in results:
        href = r.get("href", "")
        title = r.get("title", "").strip()
        # 部分聚合引擎会返回埋点跳转链接、绕过 site: 的站外结果，或直接落到首页，逐一过滤
        if not href.startswith("http") or not title:
            continue
        if domain and domain not in href:
            continue
        if urlparse(href).path in ("", "/"):
            continue
        candidates.append({
            "dept": dept_code,
            "dept_name": resolve_display_name(dept_code),
            "title": title,
            "url": href,
            "date": _normalize_date(m.group(1)) if (m := _URL_DATE_RE.search(href)) else None,
            "source_tier": "web_search",
        })
    return candidates


def search_department(dept_code: str, keywords: list[str], max_results: int, timelimit: str) -> list[dict]:
    print(f"[{dept_code}] {resolve_display_name(dept_code)}...", file=sys.stderr)

    candidates = official_site_candidates(dept_code, keywords)
    if len(candidates) < max_results:
        candidates += web_search_candidates(dept_code, keywords, max_results - len(candidates), timelimit)

    print(f"[{dept_code}] {len(candidates)} 条候选", file=sys.stderr)
    return candidates[:max_results]


def parallel_search(dept_codes: list[str], keywords: list[str], max_results: int, timelimit: str, max_workers: int) -> list[dict]:
    print(f"并行检索 {len(dept_codes)} 个部委（max_workers={max_workers}）...", file=sys.stderr)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(search_department, code, keywords, max_results, timelimit): code
            for code in dept_codes
        }
        all_results: list[dict] = []
        for future in concurrent.futures.as_completed(futures):
            code = futures[future]
            try:
                all_results.extend(future.result())
            except Exception as e:
                print(f"[{code}] 检索异常: {e}", file=sys.stderr)

    deduped: dict[str, dict] = {}
    for item in all_results:
        deduped.setdefault(item["url"], item)
    return list(deduped.values())


def main() -> None:
    known_codes = ", ".join(sorted({*DEPARTMENTS, *ASSOCIATED_DOMAINS}))
    parser = argparse.ArgumentParser(description="统一政策检索，官网直连优先，网页检索兜底")
    parser.add_argument(
        "--dept", "-d", required=True,
        help=f"逗号分隔的部委代码。已知代码: {known_codes}；未知代码仍可用，仅走网页检索",
    )
    parser.add_argument("--keywords", "-k", default="", help="检索关键词，空格分隔多个词")
    parser.add_argument("--max-results", "-n", type=int, default=5, help="每个部委返回的最大候选数")
    parser.add_argument(
        "--timelimit", "-t", default="y", choices=TIMELIMIT_CHOICES,
        help="网页检索的时间范围: d 天 / w 周 / m 月 / y 年",
    )
    parser.add_argument("--max-workers", "-w", type=int, default=5)
    args = parser.parse_args()

    dept_codes = [c.strip() for c in args.dept.split(",") if c.strip()]
    keywords = args.keywords.split()

    results = parallel_search(dept_codes, keywords, args.max_results, args.timelimit, args.max_workers)

    official = sum(1 for r in results if r["source_tier"] == "official_site")
    print(f"共 {len(results)} 条候选（官网直连 {official} / 网页搜索 {len(results) - official}）", file=sys.stderr)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
