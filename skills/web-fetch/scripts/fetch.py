#!/usr/bin/env python3
# /// script
# dependencies = ["pypdf>=4.0", "curl_cffi>=0.7"]
# ///
"""
并行抓取多个 URL，返回清洗后的正文（HTML 走正文提取，PDF 走文本抽取）。
底层四层引擎自动降级，见 engines.py 顶部说明。

CLI:
  uv run scripts/fetch.py --urls '["https://...", "https://..."]'
  uv run scripts/fetch.py --urls '["https://..."]' --max-chars 5000 --engine curl_cffi
  uv run scripts/fetch.py --urls '["https://...json 接口或列表页"]' --raw
  uv run scripts/fetch.py --urls '["https://..."]' --no-reader-proxy   # 不把 URL 交给第三方代理
  uv run scripts/fetch.py --check-env

输出 JSON: [{url, engine_used, type, title, length, truncated?, degraded, text} | {url, error, tried, engines_available?}]
--raw 模式输出: [{url, engine_used, status, content_type, degraded, length, raw} | {url, error, tried}]

字段说明：
  engine_used  实际生效的引擎：urllib / curl_cffi / reader_proxy / playwright
               reader_proxy 表示正文由远端渲染代理（默认 r.jina.ai）渲染后转交，不是原站直出，
               引用时需注明这一来源
  degraded     true 表示该 URL 需要升级到增强引擎才抓到内容；若部署环境缺失该层，
               这个 URL 会直接失败（对照 --check-env 的探测结果判断风险）
  type         html 或 pdf
  low_confidence（仅 PDF）true 表示疑似加密/扫描件，抽取结果不可靠，建议改用其他方式获取原文
  raw（仅 --raw）解码后的原始响应体，不清洗不截断，供调用方自行解析 JSON 或正则提取链接
"""
import argparse
import concurrent.futures
import json
import os
import sys

from engines import ENGINE_NAMES, READER_ENDPOINT, fetch_one, fetch_raw, probe_engines


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
    endpoint = os.environ.get("WEB_FETCH_READER_ENDPOINT") or READER_ENDPOINT
    report = {
        **avail,
        "reader_endpoint": endpoint,
        "reader_api_key": bool(os.environ.get("JINA_API_KEY")),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    notes = []
    if not avail["curl_cffi"]:
        notes.append("curl_cffi 已声明为脚本依赖，仍探测不到说明 uv 未装上依赖，检查网络或 uv cache")
    if not avail["reader_proxy"]:
        notes.append("reader_proxy 已被 WEB_FETCH_NO_READER_PROXY 禁用，JS 挑战站点将只能靠 playwright")
    if not avail["chromium"]:
        notes.append("chromium 缺失：前三层都不过关时会自动下载安装（WEB_FETCH_NO_AUTO_INSTALL=1 可禁止）")
    if not report["reader_api_key"]:
        notes.append("未配置 JINA_API_KEY：reader_proxy 仍可用，但有速率限制")
    if notes:
        print("\n提示：", file=sys.stderr)
        for line in notes:
            print(f"  {line}", file=sys.stderr)
    else:
        print("\n四层引擎均可用。", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="并行抓取多个 URL，四层引擎自动降级")
    parser.add_argument("--urls", "-u", help="JSON 字符串数组")
    parser.add_argument("--max-chars", "-m", type=int, default=8000, help="单篇正文最大字符数")
    parser.add_argument("--max-pages", type=int, default=30, help="PDF 最多读取的页数")
    parser.add_argument("--engine", choices=["auto", *ENGINE_NAMES], default="auto")
    parser.add_argument("--max-workers", "-w", type=int, default=5)
    parser.add_argument(
        "--raw", action="store_true",
        help="返回解码后的原始响应体（不清洗不截断），供调用方自行解析 JSON 或提取链接；不支持 PDF",
    )
    parser.add_argument(
        "--no-reader-proxy", action="store_true",
        help="禁用远端渲染代理层，目标 URL 不外发给第三方",
    )
    parser.add_argument(
        "--no-auto-install", action="store_true",
        help="禁止链尾兜底时自动下载安装 playwright/chromium",
    )
    parser.add_argument("--check-env", action="store_true", help="探测各层引擎可用性后退出")
    args = parser.parse_args()

    if args.no_reader_proxy:
        os.environ["WEB_FETCH_NO_READER_PROXY"] = "1"
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

    payload = parallel_fetch(urls, args.max_chars, args.max_pages, args.engine, args.max_workers, args.raw)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
