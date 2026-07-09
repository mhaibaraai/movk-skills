#!/usr/bin/env python3
"""
统一政策检索入口，纯标准库、零 API Key、无境外出网。两层检索并行编排：

  政策文件库  国务院政策文件库检索接口，覆盖全部部委，支持关键词检索，附带发文机关与文号。
  官网列表页  部委官网最新文件列表，无关键词检索能力，但能捞到政策文件库不收录的
              征求意见稿与通报。仅 ndrc / miit / mem 配置了抓取规则。

两层各自最多返回 max_results 条，互不挤占。任一层失败都不会中断另一层。

CLI:
  uv run scripts/search.py --dept ndrc,miit,mem --keywords "节能减排" --max-results 5
  uv run scripts/search.py --dept mee --keywords "碳排放 交易" --timelimit m

输出 JSON: {"results": [...], "errors": [...]}
  results[] 含 dept, dept_name, title, url, date, puborg, pcode, source_tier
            source_tier 取值 policy_library 或 official_site
  errors[]  含 dept, tier, kind, detail, site_domain
            kind 取值 no_match（该层无匹配）、network_unreachable、http_error、invalid_response
            除 no_match 外均表示该层不可用，调用方应改用外部网页搜索工具。
"""

import argparse
import concurrent.futures
import datetime
import json
import sys
import urllib.error
import urllib.parse
from urllib.parse import urljoin

from departments import DEPARTMENTS, POLICY_LIBRARY_URL, resolve_display_name, resolve_site_domain
from fetch import fetch_raw, strip_tags

# 默认 all：现行有效的政策不因发布年限而失效，按发布时间硬筛会丢掉仍在执行的旧文件。
# 结果本就按发布时间倒序，只在用户明确限定时间范围时才收窄。
TIMELIMIT_CHOICES = ("d", "w", "m", "y", "all")
_TIMELIMIT_DAYS = {"d": 1, "w": 7, "m": 31, "y": 366}

# 接口不支持按发文机关过滤，只能多取一页再在客户端筛
_LIBRARY_PAGE_SIZE = 50


def _normalize_date(date_hint: str | None) -> str | None:
    if not date_hint:
        return None
    if len(date_hint) == 8:
        return f"{date_hint[0:4]}-{date_hint[4:6]}-{date_hint[6:8]}"
    if len(date_hint) == 4:
        return date_hint
    return None


def _classify(exc: Exception) -> tuple[str, str]:
    """把异常归类为 errors[].kind，供调用方决定是重试关键词还是改用外部搜索。"""
    if isinstance(exc, urllib.error.HTTPError):
        return "http_error", f"HTTP {exc.code}"
    if isinstance(exc, OSError):  # URLError / socket.timeout / ConnectionError 均在此
        return "network_unreachable", str(getattr(exc, "reason", exc))
    return "invalid_response", f"{type(exc).__name__}: {exc}"


def _error(dept_code: str, tier: str, kind: str, detail: str) -> dict:
    return {
        "dept": dept_code,
        "tier": tier,
        "kind": kind,
        "detail": detail,
        "site_domain": resolve_site_domain(dept_code),
    }


def policy_library_candidates(dept_code: str, keywords: list[str], max_results: int, timelimit: str) -> list[dict]:
    """检索国务院政策文件库。searchfield=title 限定标题匹配，否则全文检索会淹没在无关结果里。"""
    dept = DEPARTMENTS[dept_code]
    query = urllib.parse.urlencode({
        "t": dept.library_type,
        "q": " ".join(keywords),
        "p": 1,
        "n": _LIBRARY_PAGE_SIZE,
        "timetype": "timeqb",
        "searchfield": "title",
        "sort": "pubtime",
    })
    payload = json.loads(fetch_raw(f"{POLICY_LIBRARY_URL}?{query}"))
    items = (payload.get("searchVO") or {}).get("listVO") or []

    days = _TIMELIMIT_DAYS.get(timelimit)
    earliest = datetime.date.today() - datetime.timedelta(days=days) if days else None
    candidates = []
    for item in items:
        url = item.get("url") or ""
        pubtime = item.get("pubtime")
        if not url or not pubtime:
            continue

        published = datetime.date.fromtimestamp(pubtime / 1000)
        if earliest and published < earliest:
            continue

        puborg = item.get("puborg") or ""
        if dept.puborg_keys and not any(key in puborg for key in dept.puborg_keys):
            continue

        candidates.append({
            "dept": dept_code,
            "dept_name": dept.name,
            "title": strip_tags(item.get("title") or ""),
            "url": url,
            "date": published.isoformat(),
            "puborg": puborg,
            "pcode": item.get("pcode") or "",
            "source_tier": "policy_library",
        })
        if len(candidates) >= max_results:
            break
    return candidates


def official_site_candidates(dept_code: str, keywords: list[str], max_results: int) -> list[dict]:
    """抓部委官网列表页，正则提取候选文档，按关键词过滤标题。"""
    dept = DEPARTMENTS[dept_code]
    if dept.listing is None:
        return []

    html_text = fetch_raw(dept.listing.url)
    candidates = []
    for href, date_hint, title in dept.listing.link_pattern.findall(html_text):
        title = title.strip()
        if keywords and not any(kw in title for kw in keywords):
            continue
        candidates.append({
            "dept": dept_code,
            "dept_name": dept.name,
            "title": title,
            "url": urljoin(dept.listing.url, href),
            "date": _normalize_date(date_hint),
            "puborg": "",
            "pcode": "",
            "source_tier": "official_site",
        })
        if len(candidates) >= max_results:
            break
    return candidates


def _run_tier(dept_code: str, tier: str, runner, errors: list[dict]) -> list[dict]:
    try:
        found = runner()
    except Exception as exc:
        kind, detail = _classify(exc)
        errors.append(_error(dept_code, tier, kind, detail))
        print(f"  [{dept_code}] {tier} 失败（{kind}）: {detail}", file=sys.stderr)
        return []

    if not found:
        errors.append(_error(dept_code, tier, "no_match", "该层无匹配结果"))
    return found


def search_department(dept_code: str, keywords: list[str], max_results: int, timelimit: str) -> tuple[list[dict], list[dict]]:
    print(f"[{dept_code}] {resolve_display_name(dept_code)}...", file=sys.stderr)
    errors: list[dict] = []

    candidates = _run_tier(
        dept_code, "policy_library", errors=errors,
        runner=lambda: policy_library_candidates(dept_code, keywords, max_results, timelimit),
    )
    if DEPARTMENTS[dept_code].listing is not None:
        candidates += _run_tier(
            dept_code, "official_site", errors=errors,
            runner=lambda: official_site_candidates(dept_code, keywords, max_results),
        )

    print(f"[{dept_code}] {len(candidates)} 条候选", file=sys.stderr)
    return candidates, errors


def parallel_search(dept_codes: list[str], keywords: list[str], max_results: int, timelimit: str, max_workers: int) -> dict:
    print(f"并行检索 {len(dept_codes)} 个部委（max_workers={max_workers}）...", file=sys.stderr)
    all_results: list[dict] = []
    all_errors: list[dict] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(search_department, code, keywords, max_results, timelimit): code
            for code in dept_codes
        }
        for future in concurrent.futures.as_completed(futures):
            code = futures[future]
            try:
                results, errors = future.result()
            except Exception as exc:
                kind, detail = _classify(exc)
                all_errors.append(_error(code, "search", kind, detail))
                print(f"[{code}] 检索异常: {detail}", file=sys.stderr)
                continue
            all_results.extend(results)
            all_errors.extend(errors)

    deduped: dict[str, dict] = {}
    for item in all_results:
        deduped.setdefault(item["url"], item)
    return {"results": list(deduped.values()), "errors": all_errors}


def main() -> None:
    known_codes = ", ".join(sorted(DEPARTMENTS))
    parser = argparse.ArgumentParser(description="统一政策检索，政策文件库 + 部委官网列表页")
    parser.add_argument("--dept", "-d", required=True, help=f"逗号分隔的部委代码，可选: {known_codes}")
    parser.add_argument("--keywords", "-k", default="", help="检索关键词，空格分隔多个词")
    parser.add_argument("--max-results", "-n", type=int, default=5, help="每个部委每层返回的最大候选数")
    parser.add_argument(
        "--timelimit", "-t", default="all", choices=TIMELIMIT_CHOICES,
        help="政策文件库的发布时间范围: d 天 / w 周 / m 月 / y 年 / all 不限（默认，含仍现行的旧政策）",
    )
    parser.add_argument("--max-workers", "-w", type=int, default=5)
    args = parser.parse_args()

    dept_codes = [c.strip() for c in args.dept.split(",") if c.strip()]
    unknown = [c for c in dept_codes if c not in DEPARTMENTS]
    if unknown:
        parser.error(f"未知部委代码 {', '.join(unknown)}；可选: {known_codes}")

    payload = parallel_search(dept_codes, args.keywords.split(), args.max_results, args.timelimit, args.max_workers)

    results = payload["results"]
    library = sum(1 for r in results if r["source_tier"] == "policy_library")
    blocked = sum(1 for e in payload["errors"] if e["kind"] != "no_match")
    print(
        f"共 {len(results)} 条候选（政策文件库 {library} / 官网列表页 {len(results) - library}），"
        f"{blocked} 层检索不可用",
        file=sys.stderr,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
