#!/usr/bin/env python3
# /// script
# dependencies = ["pypdf>=4.0", "python-docx>=1.1"]
# ///
"""
把一份压缩包（或单个文件）变成结构化的文本材料：下载 → 解压 → 分派抽取 → 图片 OCR → JSON。

CLI:
  uv run scripts/extract.py --url https://<平台域名>/api/file/<id>
  uv run scripts/extract.py --path ./合同包.zip --ocr off
  uv run scripts/extract.py --path ./合同包.zip --out-dir ./材料
  uv run scripts/extract.py --check-env

输出 JSON:
  {source: {kind, value, bytes, archive},
   files: [{path, ext, kind, chars, truncated, ocr, text, pages?, pages_read?}],
   skipped: [{path, reason}],
   errors: [{path, kind, detail}]}

errors[].kind:
  download_failed   URL 取不到（网络、超时、状态码非 200）
  too_large         下载体积或解压后累计体积超限
  bad_archive       不是有效压缩包 / 压缩包损坏
  unsupported_ext   扩展名不在支持范围（doc/xls/ppt 等），未处理
  parse_failed      单个文件抽取失败（损坏、加密等）
  empty_text        抽出内容接近空（PDF 疑似扫描件且 OCR 未启用或未成功）
  ocr_unavailable   OCR 被关闭，或 rapidocr / opencv 依赖不可用
  ocr_failed        单张图片 OCR 执行失败

skipped[].reason: system_noise / entry_limit / nesting_limit

前三个 kind 出现在顶层时是整体失败（退出码 1），其余都是单文件级别、不中断其余材料的处理。
进度日志走 stderr、JSON 走 stdout：用管道解析 JSON 时不要 2>&1。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import archive
import readers

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_CHARS = 40000
DOWNLOAD_TIMEOUT = 60
OCR_TIMEOUT = 600
SCRIPTS_DIR = Path(__file__).resolve().parent


class FatalError(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def log(message: str) -> None:
    print(f"[file-extract] {message}", file=sys.stderr)


def download(url: str, max_bytes: int) -> bytes:
    """下载原始字节。平台的 /api/file/<id> 直接返回文件本体，没有 JSON 信封。"""
    request = urllib.request.Request(url, headers={"User-Agent": "file-extract/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise FatalError("too_large", f"下载体积超过 {max_bytes // 1024 // 1024}MB 上限")
                chunks.append(chunk)
    except urllib.error.HTTPError as exc:
        raise FatalError("download_failed", f"HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise FatalError("download_failed", f"网络不可达: {exc.reason}") from exc
    except TimeoutError as exc:
        raise FatalError("download_failed", f"下载超时（{DOWNLOAD_TIMEOUT}s）") from exc
    return b"".join(chunks)


def load_source(args) -> tuple[bytes, dict]:
    if args.url:
        log(f"下载 {args.url}")
        data = download(args.url, args.max_bytes)
        name = args.url.split("?")[0].rsplit("/", 1)[-1]
        source = {"kind": "url", "value": args.url}
    else:
        path = Path(args.path)
        if not path.is_file():
            raise FatalError("download_failed", f"本地文件不存在: {path}")
        data = path.read_bytes()
        if len(data) > args.max_bytes:
            raise FatalError("too_large", f"文件体积超过 {args.max_bytes // 1024 // 1024}MB 上限")
        name = path.name
        source = {"kind": "path", "value": str(path)}
    source["bytes"] = len(data)
    source["archive"] = archive.is_zip(data)
    return data, {"source": source, "name": name}


def run_ocr(targets: list[dict], scripts_dir: Path) -> dict[str, dict]:
    """把待识别文件写进临时目录，交给独立入口 ocr.py 处理。

    单独起子进程而不是 import：OCR 依赖约 150MB，做成独立入口后 --ocr off 的
    运行完全不必安装它们。
    """
    uv = shutil.which("uv")
    if not uv:
        return {t["path"]: {"kind": "ocr_unavailable", "detail": "找不到 uv，无法拉起 OCR 子进程"}
                for t in targets}

    with tempfile.TemporaryDirectory(prefix="file-extract-ocr-") as tmp:
        mapping: dict[str, dict] = {}
        for index, target in enumerate(targets):
            temp_path = os.path.join(tmp, f"{index:03d}.{target['ext']}")
            with open(temp_path, "wb") as handle:
                handle.write(target["data"])
            mapping[temp_path] = target

        log(f"OCR {len(mapping)} 个文件（首次运行需安装约 150MB 依赖，可能耗时较久）")
        process = subprocess.run(
            [uv, "run", "--quiet", str(scripts_dir / "ocr.py"), "--paths", json.dumps(list(mapping))],
            capture_output=True, text=True, timeout=OCR_TIMEOUT
        )
        if process.stderr:
            print(process.stderr.rstrip(), file=sys.stderr)

        if process.returncode != 0:
            detail = _unavailable_detail(process.stdout) or f"ocr.py 退出码 {process.returncode}"
            return {t["path"]: {"kind": "ocr_unavailable", "detail": detail} for t in targets}

        try:
            payload = json.loads(process.stdout)
        except json.JSONDecodeError:
            return {t["path"]: {"kind": "ocr_failed", "detail": "ocr.py 输出不是合法 JSON"}
                    for t in targets}

    outcome: dict[str, dict] = {}
    for item in payload.get("results", []):
        target = mapping.get(item["path"])
        if not target:
            continue
        outcome[target["path"]] = item.get("error") or {"text": item.get("text", "")}
    return outcome


def _unavailable_detail(stdout: str) -> str:
    try:
        status = json.loads(stdout).get("unavailable") or {}
    except (json.JSONDecodeError, AttributeError):
        return ""
    reasons = [v for k, v in status.items() if k.endswith("_detail")]
    return "OCR 依赖不可用" + (f": {reasons[0]}" if reasons else "")


def check_env(scripts_dir: Path) -> int:
    status: dict[str, object] = {}
    for module, key in (("pypdf", "pypdf"), ("docx", "python_docx")):
        try:
            __import__(module)
            status[key] = True
        except Exception as exc:
            status[key] = False
            status[f"{key}_detail"] = f"{type(exc).__name__}: {exc}"

    uv = shutil.which("uv")
    if uv:
        log("探测 OCR 链路（首次会安装约 150MB 依赖）")
        process = subprocess.run(
            [uv, "run", "--quiet", str(scripts_dir / "ocr.py"), "--check-env"],
            capture_output=True, text=True, timeout=OCR_TIMEOUT
        )
        try:
            status["ocr"] = json.loads(process.stdout)
        except json.JSONDecodeError:
            status["ocr"] = {"error": process.stderr.strip()[-500:]}
    else:
        status["ocr"] = {"error": "找不到 uv"}

    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status.get("pypdf") and status.get("python_docx") else 1


def build(args) -> dict:
    data, meta = load_source(args)
    try:
        files, skipped, errors = archive.unpack(
            data, meta["name"], max_total_bytes=args.max_total_bytes
        )
    except Exception as exc:
        raise FatalError("bad_archive", f"压缩包无法解压: {exc}") from exc

    files.sort(key=lambda item: item["path"])
    log(f"解出 {len(files)} 个可处理文件，跳过 {len(skipped)} 个")

    results: list[dict] = []
    ocr_targets: list[dict] = []

    for item in files:
        if item["kind"] == "image":
            ocr_targets.append(item)
            continue
        try:
            extracted = readers.read(item["kind"], item["ext"], item["data"], args.max_chars)
        except readers.ExtractError as exc:
            errors.append({"path": item["path"], "kind": exc.kind, "detail": exc.detail})
            continue

        low_text = extracted.pop("low_text", False)
        entry = {"path": item["path"], "ext": item["ext"], "kind": "document", "ocr": False}
        entry.update(extracted)
        if low_text and args.ocr != "off":
            ocr_targets.append({**item, "_fallback": entry})
        elif low_text:
            errors.append({"path": item["path"], "kind": "empty_text",
                           "detail": "PDF 抽出文本接近空，疑似扫描件；--ocr off 未启用 OCR"})
        else:
            results.append(entry)

    if args.ocr == "on":
        for item in files:
            if item["ext"] == "pdf" and not any(t["path"] == item["path"] for t in ocr_targets):
                ocr_targets.append(item)
                results = [r for r in results if r["path"] != item["path"]]

    if ocr_targets:
        if args.ocr == "off":
            for item in ocr_targets:
                errors.append({"path": item["path"], "kind": "ocr_unavailable",
                               "detail": "OCR 已关闭（--ocr off），该文件未识别"})
        else:
            outcome = run_ocr(ocr_targets, SCRIPTS_DIR)
            for item in ocr_targets:
                got = outcome.get(item["path"], {"kind": "ocr_failed", "detail": "OCR 未返回该文件的结果"})
                if "kind" in got:
                    errors.append(_failure(item, got["kind"], got.get("detail", ""), results))
                    continue
                text = got.get("text", "")[:args.max_chars]
                if not text.strip():
                    errors.append(_failure(item, "empty_text",
                                           "OCR 未识别出文字，可能是无文字内容的图片", results))
                    continue
                results.append({
                    "path": item["path"], "ext": item["ext"],
                    "kind": item["kind"], "ocr": True,
                    "chars": len(text), "truncated": False, "text": text
                })

    if args.out_dir:
        _dump(files, Path(args.out_dir))

    results.sort(key=lambda item: item["path"])
    errors.sort(key=lambda item: item["path"])
    return {"source": meta["source"], "files": results, "skipped": skipped, "errors": errors}


def _failure(item: dict, kind: str, detail: str, results: list[dict]) -> dict:
    """记录单文件失败。无文本层 PDF 走 OCR 兜底又失败时，把文本层那点残留结果放回去，
    并在 detail 里说明已回退——别让调用方以为这份材料完全没有内容。"""
    fallback = item.get("_fallback")
    if fallback:
        results.append(fallback)
        detail = f"{detail}；已回退到文本层抽取结果"
    return {"path": item["path"], "kind": kind, "detail": detail}


def _dump(files: list[dict], out_dir: Path) -> None:
    """把解出的原始文件落盘。压缩包内的路径不可信，逐段清洗后再拼，避免 zip slip。"""
    for item in files:
        parts = [p for p in item["path"].split("/") if p not in ("", ".", "..")]
        target = out_dir.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item["data"])
    log(f"原始文件已写入 {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="压缩包 / 单文件 → 结构化文本材料")
    parser.add_argument("--url", "-u", help="文件的完整 URL")
    parser.add_argument("--path", "-p", help="本地文件路径")
    parser.add_argument("--ocr", choices=["auto", "on", "off"], default="auto",
                        help="auto=图片与无文本层 PDF 走 OCR；on=所有 PDF 也强制 OCR；off=完全不用")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="单个文件的文本上限")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="下载体积上限")
    parser.add_argument("--max-total-bytes", type=int, default=archive.MAX_TOTAL_BYTES,
                        help="解压后累计字节上限")
    parser.add_argument("--out-dir", help="把解出的原始文件写到该目录")
    parser.add_argument("--check-env", action="store_true", help="探测依赖可用性后退出")
    args = parser.parse_args()

    if args.check_env:
        sys.exit(check_env(SCRIPTS_DIR))
    if bool(args.url) == bool(args.path):
        parser.error("--url 与 --path 二选一")

    try:
        payload = build(args)
    except FatalError as exc:
        print(json.dumps({"error": {"kind": exc.kind, "detail": exc.detail}}, ensure_ascii=False))
        log(f"失败: {exc.kind} {exc.detail}")
        sys.exit(1)

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
