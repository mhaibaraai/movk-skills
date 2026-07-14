#!/usr/bin/env python3
"""
统一政策检索入口。两层检索来源：

  政策文件库  国务院政策文件库检索接口，覆盖全部部委，支持关键词检索，附带发文机关与文号。
  官网列表页  部委官网最新文件列表，无关键词检索能力，但能捞到政策文件库不收录的
              征求意见稿与通报。仅 ndrc / miit / mem 配置了抓取规则。

本脚本只负责「构造检索 URL + 解析响应」，网络抓取全部委托 web-fetch 基座技能的
`fetch.py --raw`（一次批量取回全部部委的原始响应体，并发与两层引擎降级由基座负责）。
部署时 web-fetch 需与本技能同级放在 skills/ 下。

两层各自最多返回 max_results 条，互不挤占。任一层失败都不会中断另一层。

CLI:
  uv run scripts/search.py --dept ndrc,miit,mem --keywords "节能减排" --max-results 5
  uv run scripts/search.py --dept mee --keywords "碳排放 交易" --timelimit m

输出 JSON: {"results": [...], "errors": [...]}
  results[] 含 dept, dept_name, title, url, date, puborg, pcode, source_tier
            source_tier 取值 policy_library 或 official_site
  errors[]  含 dept, tier, kind, detail, site_domain
            kind 取值 no_match（该层确实无匹配政策）、network_unreachable、http_error、
                      blocked（各层引擎均命中反爬/验证页）、invalid_response（该层取到了页面
                      但解析不出东西：JSON 变了，或官网改版让 link_pattern 失效）
            除 no_match 外均表示该层不可用，调用方应改用 web-fetch 的 360 检索兜底，
            不得当作"该部委没有这类政策"。
"""

import argparse
import datetime
import html
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from urllib.parse import urljoin

from departments import DEPARTMENTS, POLICY_LIBRARY_URL, resolve_display_name, resolve_site_domain

WEB_FETCH = Path(__file__).resolve().parents[2] / "web-fetch" / "scripts" / "fetch.py"

# 默认 all：现行有效的政策不因发布年限而失效，按发布时间硬筛会丢掉仍在执行的旧文件。
# 结果本就按发布时间倒序，只在用户明确限定时间范围时才收窄。
TIMELIMIT_CHOICES = ("d", "w", "m", "y", "all")
_TIMELIMIT_DAYS = {"d": 1, "w": 7, "m": 31, "y": 366}

# 接口不支持按发文机关过滤，只能多取一页再在客户端筛
_LIBRARY_PAGE_SIZE = 50

_TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(text: str) -> str:
    """去掉标签并反转义实体，用于清洗检索接口返回标题里的 <em> 高亮片段。"""
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _library_url(dept_code: str, keywords: list[str]) -> str:
    """政策文件库检索 URL。searchfield=title 限定标题匹配，否则全文检索会淹没在无关结果里。"""
    query = urllib.parse.urlencode({
        "t": DEPARTMENTS[dept_code].library_type,
        "q": " ".join(keywords),
        "p": 1,
        "n": _LIBRARY_PAGE_SIZE,
        "timetype": "timeqb",
        "searchfield": "title",
        "sort": "pubtime",
    })
    return f"{POLICY_LIBRARY_URL}?{query}"


def _classify(error: str) -> str:
    """把基座返回的 error 串归类为 errors[].kind，供调用方决定是重试关键词还是改用 360 检索。"""
    if "疑似反爬" in error or "验证" in error or "访问异常" in error:
        return "blocked"
    if any(k in error for k in ("URLError", "TimeoutError", "ConnectionError", "OSError",
                                "SSLError", "CertificateVerifyError")):
        return "network_unreachable"
    if "HTTPError" in error or error.startswith("HTTP "):
        return "http_error"
    return "invalid_response"


def _error(dept_code: str, tier: str, kind: str, detail: str) -> dict:
    return {
        "dept": dept_code,
        "tier": tier,
        "kind": kind,
        "detail": detail,
        "site_domain": resolve_site_domain(dept_code),
    }


def batch_raw_fetch(urls: list[str], max_workers: int) -> dict[str, dict]:
    """调 web-fetch 基座批量抓原始响应体，返回 {url: 基座结果}。"""
    if not WEB_FETCH.exists():
        sys.exit(
            f"未找到 web-fetch 基座技能（期望路径 {WEB_FETCH}）。"
            "本技能的网络抓取依赖 web-fetch，请将其与本技能同级部署在 skills/ 目录下。"
        )

    proc = subprocess.run(
        ["uv", "run", str(WEB_FETCH), "--urls", json.dumps(urls), "--raw",
         "--max-workers", str(max_workers)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"web-fetch 调用失败（exit {proc.returncode}）: {proc.stderr.strip()}")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        sys.exit(f"web-fetch 输出解析失败: {exc}")

    return {item["url"]: item for item in payload}


def policy_library_candidates(dept_code: str, raw: str, max_results: int, timelimit: str) -> list[dict]:
    dept = DEPARTMENTS[dept_code]
    payload = json.loads(raw)
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


def _normalize_date(date_hint: str | None) -> str | None:
    if not date_hint:
        return None
    if len(date_hint) == 8:
        return f"{date_hint[0:4]}-{date_hint[4:6]}-{date_hint[6:8]}"
    if len(date_hint) == 4:
        return date_hint
    return None


class ListingPatternStale(Exception):
    """列表页取到了，但 link_pattern 一条链接都没提出来。"""


def official_site_candidates(dept_code: str, raw: str, keywords: list[str], max_results: int) -> list[dict]:
    """从部委官网列表页正则提取候选文档，按关键词过滤标题。

    "一条链接都没提到"与"提到了但没有关键词命中"必须区分：前者说明页面改版让 link_pattern
    失效、或抓到的是反爬页，属于该层不可用；后者才是真的没有匹配的政策。两者都返回空列表的话，
    抓取器坏掉会被伪装成"该部委没有这类政策"，直接污染解读结论。
    """
    dept = DEPARTMENTS[dept_code]
    listing = dept.listing
    links = listing.link_pattern.findall(raw)
    if not links:
        raise ListingPatternStale(
            f"官网列表页未提取到任何链接（页面 {len(raw)} 字符）。"
            f"该页可能已改版导致 departments.py 里 {dept_code} 的 link_pattern 失效，"
            f"或抓到的是反爬页；请核对 {listing.url}"
        )

    candidates = []
    for href, date_hint, title in links:
        title = title.strip()
        if keywords and not any(kw in title for kw in keywords):
            continue
        candidates.append({
            "dept": dept_code,
            "dept_name": dept.name,
            "title": title,
            "url": urljoin(listing.url, href),
            "date": _normalize_date(date_hint),
            "puborg": "",
            "pcode": "",
            "source_tier": "official_site",
        })
        if len(candidates) >= max_results:
            break
    return candidates


def _parse_tier(dept_code: str, tier: str, fetched: dict | None, parser, errors: list[dict]) -> list[dict]:
    if fetched is None:
        errors.append(_error(dept_code, tier, "invalid_response", "web-fetch 未返回该 URL 的结果"))
        return []
    if "error" in fetched:
        kind = _classify(fetched["error"])
        errors.append(_error(dept_code, tier, kind, fetched["error"]))
        print(f"  [{dept_code}] {tier} 失败（{kind}）: {fetched['error']}", file=sys.stderr)
        return []

    try:
        found = parser(fetched["raw"])
    except ListingPatternStale as exc:
        # 抓取器坏了，不是"没有政策"——必须报为该层不可用，让调用方走 360 兜底
        errors.append(_error(dept_code, tier, "invalid_response", str(exc)))
        print(f"  [{dept_code}] {tier} 抓取规则失效: {exc}", file=sys.stderr)
        return []
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        errors.append(_error(dept_code, tier, "invalid_response", detail))
        print(f"  [{dept_code}] {tier} 解析失败: {detail}", file=sys.stderr)
        return []

    if not found:
        errors.append(_error(dept_code, tier, "no_match", "该层无匹配结果"))
    return found


def search(dept_codes: list[str], keywords: list[str], max_results: int, timelimit: str,
           max_workers: int) -> dict:
    plan: list[tuple[str, str, str]] = []  # (dept_code, tier, url)
    for code in dept_codes:
        plan.append((code, "policy_library", _library_url(code, keywords)))
        listing = DEPARTMENTS[code].listing
        if listing is not None:
            plan.append((code, "official_site", listing.url))

    print(f"检索 {len(dept_codes)} 个部委 / {len(plan)} 层，抓取交给 web-fetch...", file=sys.stderr)
    fetched_by_url = batch_raw_fetch([url for _, _, url in plan], max_workers)

    results: list[dict] = []
    errors: list[dict] = []
    for code, tier, url in plan:
        parser = (
            (lambda raw, c=code: policy_library_candidates(c, raw, max_results, timelimit))
            if tier == "policy_library"
            else (lambda raw, c=code: official_site_candidates(c, raw, keywords, max_results))
        )
        found = _parse_tier(code, tier, fetched_by_url.get(url), parser, errors)
        results.extend(found)
        print(f"[{code}] {resolve_display_name(code)} {tier}: {len(found)} 条", file=sys.stderr)

    deduped: dict[str, dict] = {}
    for item in results:
        deduped.setdefault(item["url"], item)
    return {"results": list(deduped.values()), "errors": errors}


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

    payload = search(dept_codes, args.keywords.split(), args.max_results, args.timelimit, args.max_workers)

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
