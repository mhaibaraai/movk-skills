#!/usr/bin/env python3
"""沙箱能力探针：一次跑完，报告「缩略图预览」与「自带模板取数」在本环境是否可用。

纯标准库，任何环境都能跑。在平台调试画布里让技能执行本脚本，据输出决定编排是否需要降级。

用法：
    uv run skills/create-ppt/scripts/probe_env.py
    uv run skills/create-ppt/scripts/probe_env.py --url /api/file/xxx --base https://平台域名
"""
from __future__ import annotations

import argparse
import platform
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

SOFFICE_CANDIDATES = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/opt/libreoffice/program/soffice",
)
PROBE_TIMEOUT = 30
HEAD_BYTES = 4096


def probe_renderer() -> tuple[bool, str]:
    for name in ("soffice", "libreoffice"):
        if path := shutil.which(name):
            return True, f"✓ 渲染器可用：{name} -> {path}"
    for path in SOFFICE_CANDIDATES:
        if Path(path).exists():
            return True, f"✓ 渲染器可用：{path}"
    return False, "✗ 无 soffice/LibreOffice —— 缩略图预览不可用，需降级为「文本卡片 + pptx 下载链接」"


def probe_download(url: str | None, base: str | None) -> list[str]:
    if not url:
        return ["– 未给 --url，跳过取数探测（自带模板功能需要这一步通过）"]
    full = url if url.startswith(("http://", "https://")) else (base or "").rstrip("/") + "/" + url.lstrip("/")
    if not full.startswith(("http://", "https://")):
        return [f"✗ 拼不出完整 URL（url={url!r} base={base!r}）—— 需要 --base 或完整 URL"]

    lines = [f"  目标 URL：{full}"]
    tmp = Path("probe_download.bin")
    try:
        req = urllib.request.Request(full, headers={"User-Agent": "create-ppt/probe"})
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
            status = getattr(resp, "status", "?")
            ctype = resp.headers.get("Content-Type", "?")
            data = resp.read()
        tmp.write_bytes(data)
        lines.append(f"✓ 下载成功：HTTP {status}，{len(data)} 字节，Content-Type={ctype}")
        if zipfile.is_zipfile(tmp):
            with zipfile.ZipFile(tmp) as z:
                is_pptx = "ppt/presentation.xml" in z.namelist()
            lines.append("✓ 是有效 pptx 包（可作模板）" if is_pptx else "△ 是 zip 但不是 pptx（缺 ppt/presentation.xml）")
        else:
            lines.append("△ 不是 zip/pptx —— 可能是错误信息，也可能是包着真实下载地址的 JSON 信封")
            # 响应体是判断「取数要不要多一跳」的唯一依据，必须原样打出来
            body = data[:800].decode("utf-8", "replace")
            lines.append(f"  响应体（前 {len(body)} 字符）：{body}")
    except Exception as exc:  # 探针要如实报告任何失败原因，不吞异常
        lines.append(f"✗ 下载失败：{type(exc).__name__}: {exc}")
        lines.append("   自带模板功能依赖此通路；失败则需换取数方式（确认 base 域名、鉴权、内网可达性）")
    finally:
        tmp.unlink(missing_ok=True)
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="平台给的文件相对路径或完整 URL（如 /api/file/xxx）")
    parser.add_argument("--base", help="平台域名（如 https://llm.geosophon.com）")
    args = parser.parse_args()

    print("=== create-ppt 沙箱能力探针 ===")
    print(f"平台：{platform.platform()}")
    print(f"Python：{sys.version.split()[0]}")
    print(f"工作目录：{Path.cwd()}")
    skill = Path("skills/create-ppt")
    print(f"技能前缀 skills/create-ppt/ 存在：{'是' if skill.exists() else '否（脚本前缀需改用实际路径）'}")

    print("\n--- 1. 缩略图预览（需系统预装 LibreOffice，uv 装不了）---")
    ok_render, msg = probe_renderer()
    print(msg)

    print("\n--- 2. 自带模板取数（下载用户上传的 pptx）---")
    for line in probe_download(args.url, args.base):
        print(line)

    print("\n--- 结论 ---")
    print(f"缩略图预览：{'可用' if ok_render else '不可用 → 编排降级为文本卡片 + 下载链接'}")
    print("自带模板：见上方取数结果；下载通路不通则该功能不可用")
    print("\n以下两项脚本探测不到，需在调试画布里人工确认：")
    print("  a) 回复里的 Markdown 图片链接能否正常渲染（决定缩略图怎么呈现给用户）")
    print("  b) string 全局变量 g_source 能否完整装下文档提取的长正文")


if __name__ == "__main__":
    main()
