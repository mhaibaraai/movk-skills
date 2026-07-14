#!/usr/bin/env python3
# /// script
# dependencies = ["pypdf>=4.0", "curl_cffi>=0.7"]
# ///
"""
批量抓取多个 URL，返回清洗后的正文（HTML 走正文提取，PDF 走文本抽取）。
底层两层引擎自动降级，见 engines.py 顶部说明。

CLI:
  uv run scripts/fetch.py --urls '["https://...", "https://..."]'
  uv run scripts/fetch.py --urls '["https://..."]' --max-chars 5000 --engine http
  uv run scripts/fetch.py --urls '["https://...json 接口或列表页"]' --raw
  uv run scripts/fetch.py --check-env

输出 JSON: [{url, engine_used, type, title, length, truncated?, degraded, text} | {url, error, attempts}]
--raw 模式输出: [{url, engine_used, status, content_type, degraded, length, raw} | {url, error, attempts}]

字段说明：
  engine_used  实际生效的引擎：http（curl_cffi 直发）或 browser（playwright 渲染）
  degraded     true 表示该 URL 必须靠浏览器渲染才拿得到；部署环境装不了浏览器时这类 URL 会失败
  type         html 或 pdf
  attempts     仅失败时出现，逐层列出失败原因 [{engine, kind, detail}]
               kind: http_error / challenge / empty_body / unexpected_structure / timeout /
                     network / too_large
  low_confidence（仅 PDF）true 表示疑似加密/扫描件，抽取结果不可靠，建议改用其他方式获取原文
  raw（仅 --raw）解码后的原始响应体，不清洗不截断，供调用方自行解析 JSON 或提取链接

正文低于 MIN_TEXT_CHARS 字符一律判失败而非返回空壳——挑战未通过的页面往往只剩一个标题，
把它当成功返回会让调用方拿着空内容做分析。
"""
import argparse
import json
import os
import sys

from engines import DEFAULT_CONCURRENCY, ENGINE_NAMES, fetch_many, probe_engines


def print_check_env() -> None:
    avail = probe_engines()
    print(json.dumps(avail, ensure_ascii=False, indent=2))

    notes = []
    if not avail["http"]:
        notes.append("curl_cffi 已声明为脚本依赖，仍探测不到说明 uv 未装上依赖，检查网络或 uv cache")
    if not avail["chromium"]:
        notes.append("浏览器缺失：http 层不过关时会按需安装 headless shell"
                     "（WEB_FETCH_NO_AUTO_INSTALL=1 可禁止）；装不上则 JS 渲染类站点抓不到")
    if notes:
        print("\n提示：", file=sys.stderr)
        for line in notes:
            print(f"  {line}", file=sys.stderr)
    else:
        print("\n两层引擎均可用。", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="批量抓取多个 URL，两层引擎自动降级")
    parser.add_argument("--urls", "-u", help="JSON 字符串数组")
    parser.add_argument("--max-chars", "-m", type=int, default=8000, help="单篇正文最大字符数")
    parser.add_argument("--max-pages", type=int, default=30, help="PDF 最多读取的页数")
    parser.add_argument("--engine", choices=["auto", *ENGINE_NAMES], default="auto")
    parser.add_argument("--max-workers", "-w", type=int, default=DEFAULT_CONCURRENCY,
                        help="并发上限：http 层的线程数，也是同一浏览器实例下并发的 page 数")
    parser.add_argument(
        "--raw", action="store_true",
        help="返回解码后的原始响应体（不清洗不截断），供调用方自行解析 JSON 或提取链接；不支持 PDF",
    )
    parser.add_argument(
        "--no-auto-install", action="store_true",
        help="禁止在 http 层不过关时自动安装 playwright/浏览器",
    )
    parser.add_argument("--check-env", action="store_true", help="探测各层引擎可用性后退出")
    args = parser.parse_args()

    if args.no_auto_install:
        os.environ["WEB_FETCH_NO_AUTO_INSTALL"] = "1"

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

    deduped = list(dict.fromkeys(urls))
    mode = "raw" if args.raw else "clean"
    print(f"抓取 {len(deduped)} 个页面（engine={args.engine}, mode={mode}, max_workers={args.max_workers}）...", file=sys.stderr)

    results = fetch_many(deduped, args.max_chars, args.max_pages, args.engine,
                         raw=args.raw, max_workers=args.max_workers)

    succeeded = sum(1 for r in results if "error" not in r)
    degraded = sum(1 for r in results if r.get("degraded"))
    total_chars = sum(r.get("length", 0) for r in results)
    print(
        f"成功 {succeeded} / 失败 {len(deduped) - succeeded}（其中 {degraded} 条依赖浏览器渲染）"
        f" / 共 {total_chars} 字符",
        file=sys.stderr,
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
