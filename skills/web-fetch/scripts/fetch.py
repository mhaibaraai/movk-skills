#!/usr/bin/env python3
# /// script
# dependencies = ["pypdf>=4.0"]
# ///
"""
并行抓取多个 URL，返回清洗后的正文（HTML 走正文提取，PDF 走文本抽取）。
底层三层引擎自动降级，见 engines.py 顶部说明。

CLI:
  uv run scripts/fetch.py --urls '["https://...", "https://..."]'
  uv run scripts/fetch.py --urls '["https://..."]' --max-chars 5000 --engine curl_cffi
  uv run scripts/fetch.py --urls '["https://...json 接口或列表页"]' --raw
  uv run scripts/fetch.py --check-env

输出 JSON: [{url, engine_used, type, title, length, truncated?, degraded, text} | {url, error, tried, engines_available?}]
--raw 模式输出: [{url, engine_used, status, content_type, degraded, length, raw} | {url, error, tried}]

字段说明：
  engine_used  实际生效的引擎：urllib / curl_cffi / playwright
  degraded     true 表示该 URL 需要升级到 curl_cffi 或 playwright 才抓到内容；
               若部署环境缺失该层，这个 URL 会直接失败（对照 --check-env 的探测结果判断风险）
  type         html 或 pdf
  low_confidence（仅 PDF）true 表示疑似加密/扫描件，抽取结果不可靠，建议改用其他方式获取原文
  raw（仅 --raw）解码后的原始响应体，不清洗不截断，供调用方自行解析 JSON 或正则提取链接
"""
import argparse
import concurrent.futures
import json
import sys

from engines import fetch_one, fetch_raw, probe_engines


def parallel_fetch(urls: list[str], max_chars: int, max_pages: int, engine: str, max_workers: int,
                   raw: bool = False) -> list[dict]:
    deduped = list(dict.fromkeys(urls))
    mode = "raw" if raw else "clean"
    print(f"抓取 {len(deduped)} 个页面（engine={engine}, mode={mode}, max_workers={max_workers}）...", file=sys.stderr)

    def run(url: str) -> dict:
        if raw:
            return fetch_raw(url, engine)
        return fetch_one(url, max_chars, max_pages, engine)

    results: list[dict] = [{}] * len(deduped)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(run, u): i for i, u in enumerate(deduped)}
        for future in concurrent.futures.as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = {"url": deduped[idx], "error": str(e)}

    succeeded = sum(1 for r in results if "error" not in r)
    degraded = sum(1 for r in results if r.get("degraded"))
    total_chars = sum(r.get("length", 0) for r in results)
    print(
        f"成功 {succeeded} / 失败 {len(deduped) - succeeded}（其中 {degraded} 条依赖增强引擎）"
        f" / 共 {total_chars} 字符",
        file=sys.stderr,
    )
    return results


def print_check_env() -> None:
    avail = probe_engines()
    print(json.dumps(avail, ensure_ascii=False, indent=2))
    missing = []
    if not avail["curl_cffi"]:
        missing.append("pip install curl_cffi  # 或 uv add --script <脚本> curl_cffi")
    if not avail["playwright"]:
        missing.append("pip install playwright")
    if avail["playwright"] and not avail["chromium"]:
        missing.append("playwright install chromium  # 或确保系统已安装 Chrome")
    if missing:
        print("\n缺失能力的安装建议：", file=sys.stderr)
        for line in missing:
            print(f"  {line}", file=sys.stderr)
    else:
        print("\n三层引擎均可用。", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="并行抓取多个 URL，三层引擎自动降级")
    parser.add_argument("--urls", "-u", help="JSON 字符串数组")
    parser.add_argument("--max-chars", "-m", type=int, default=8000, help="单篇正文最大字符数")
    parser.add_argument("--max-pages", type=int, default=30, help="PDF 最多读取的页数")
    parser.add_argument("--engine", choices=["auto", "urllib", "curl_cffi", "playwright"], default="auto")
    parser.add_argument("--max-workers", "-w", type=int, default=5)
    parser.add_argument(
        "--raw", action="store_true",
        help="返回解码后的原始响应体（不清洗不截断），供调用方自行解析 JSON 或提取链接；不支持 PDF",
    )
    parser.add_argument("--check-env", action="store_true", help="探测三层引擎可用性后退出")
    args = parser.parse_args()

    if args.check_env:
        print_check_env()
        return

    if not args.urls:
        parser.error("--urls 是必填项（除非使用 --check-env）")

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

    payload = parallel_fetch(urls, args.max_chars, args.max_pages, args.engine, args.max_workers, args.raw)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
