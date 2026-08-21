#!/usr/bin/env python3
# /// script
# dependencies = []
# ///
"""
把一份压缩包（或单个文件）变成结构化的文本材料：下载 → 解压 → 分派抽取 → 图片 OCR → JSON。

脚本零第三方依赖（pdf 用 vendor/ 下的 pypdf wheel），直接 python3 调用即可，不需要 uv。

CLI:
  python3 scripts/extract.py --url https://<平台域名>/api/file/<id>
  python3 scripts/extract.py --path ./合同包.zip --ocr off
  python3 scripts/extract.py --path ./合同包.zip --out-dir ./材料
  python3 scripts/extract.py --path ./合同包.zip --platform-base https://<平台域名> --platform-token <token>
  python3 scripts/extract.py --check-env

输出 JSON:
  {source: {kind, value, bytes, archive},
   files: [{path, ext, kind, chars, truncated, ocr, text, pages?, pages_read?}],
   skipped: [{path, reason}],
   pending_ocr: [{path, ext, url, uploaded, detail}],
   errors: [{path, kind, detail}]}

pending_ocr 是「本地识别不了、脚本内也没能解决」的图片与扫描件。给了平台参数时
脚本会把它们传回平台换成公开 URL（uploaded=true），由下游节点交给视觉模型识别；
连上传都没做成的条目 uploaded=false，必须如实告诉用户，不能当成不存在。

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
import platform_api
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
    source["archive"] = archive.is_zip(data) and not archive.is_ooxml(data)
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
    """探测三条链路。docx 走标准库恒可用，所以只报 pdf 与 OCR。"""
    status: dict[str, object] = {"docx": True}
    try:
        readers.load_pypdf()
        status["pypdf"] = True
    except readers.ExtractError as exc:
        status["pypdf"] = False
        status["pypdf_detail"] = exc.detail

    # 本地 OCR 链要装约 150MB 依赖，只有能联网的机器装得上；沙箱离线时这里必然为不可用，
    # 图片改走平台（--platform-base/--platform-token），这是设计好的降级而不是故障。
    uv = shutil.which("uv")
    if uv:
        log("探测本地 OCR 链路（首次会安装约 150MB 依赖）")
        process = subprocess.run(
            [uv, "run", "--quiet", str(scripts_dir / "ocr.py"), "--check-env"],
            capture_output=True, text=True, timeout=OCR_TIMEOUT
        )
        try:
            status["ocr"] = json.loads(process.stdout)
        except json.JSONDecodeError:
            status["ocr"] = {"error": process.stderr.strip()[-500:]}
    else:
        status["ocr"] = {"error": "找不到 uv，本地 OCR 不可用；图片请走平台解析"}

    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status.get("pypdf") else 1


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

    client = None
    if args.platform_base and args.platform_token:
        client = platform_api.Platform(args.platform_base, args.platform_token)

    results: list[dict] = []
    ocr_targets: list[dict] = []

    for item in files:
        if item["kind"] == "image":
            ocr_targets.append(item)
            continue
        if args.parse_remote == "force" and client:
            try:
                text = client.parse(item["path"].rsplit("/", 1)[-1], item["data"])[:args.max_chars]
                results.append({"path": item["path"], "ext": item["ext"], "kind": "document",
                                "ocr": True, "chars": len(text), "truncated": False, "text": text})
                continue
            except platform_api.PlatformError as exc:
                log(f"平台解析 {item['path']} 失败，回落本地解析: {exc.detail}")
        try:
            extracted = readers.read(item["kind"], item["ext"], item["data"], args.max_chars)
        except readers.ExtractError as exc:
            errors.append({"path": item["path"], "kind": exc.kind, "detail": exc.detail})
            continue

        low_text = extracted.pop("low_text", False)
        entry = {"path": item["path"], "ext": item["ext"], "kind": "document", "ocr": False}
        entry.update(extracted)
        if low_text:
            # 疑似扫描件一律进待识别队列，由「本地 OCR → 平台解析」两级依次消化。
            # --ocr off 只关掉本地那一级，不代表这份材料就此当作读不到。
            ocr_targets.append({**item, "_fallback": entry})
        else:
            results.append(entry)

    if args.ocr == "on":
        for item in files:
            if item["ext"] == "pdf" and not any(t["path"] == item["path"] for t in ocr_targets):
                ocr_targets.append(item)
                results = [r for r in results if r["path"] != item["path"]]

    pending: list[dict] = []
    if ocr_targets:
        remaining = ocr_targets
        if args.ocr != "off" and shutil.which("uv"):
            remaining = _local_ocr(ocr_targets, args, results, errors)
        elif args.ocr == "off":
            log("--ocr off：图片与扫描件不做本地识别")
        else:
            log("找不到 uv，跳过本地 OCR")
        pending = _remote_resolve(remaining, client, args, results, errors)

    if args.out_dir:
        _dump(files, Path(args.out_dir))

    results.sort(key=lambda item: item["path"])
    errors.sort(key=lambda item: item["path"])
    pending.sort(key=lambda item: item["path"])
    return {"source": meta["source"], "files": results, "skipped": skipped,
            "pending_ocr": pending, "errors": errors}


def _local_ocr(targets: list[dict], args, results: list[dict], errors: list[dict]) -> list[dict]:
    """本地 OCR。识别成功的进 files，失败的原样退回，交给平台那条路继续处理。"""
    outcome = run_ocr(targets, SCRIPTS_DIR)
    remaining: list[dict] = []
    for item in targets:
        got = outcome.get(item["path"])
        text = (got or {}).get("text", "")[:args.max_chars] if got and "kind" not in got else ""
        if not text.strip():
            remaining.append(item)
            continue
        results.append({
            "path": item["path"], "ext": item["ext"],
            "kind": item["kind"], "ocr": True,
            "chars": len(text), "truncated": False, "text": text
        })
    return remaining


def _remote_resolve(targets: list[dict], client, args, results: list[dict],
                    errors: list[dict]) -> list[dict]:
    """本地识别不了的材料交给平台：扫描件整份送去解析，图片上传换成公开 URL。

    没给平台参数、或平台调用失败时，条目留在 pending_ocr 里如实报出——
    宁可让调用方看到「这份材料我没读到」，也不能悄悄当它不存在。
    """
    pending: list[dict] = []
    for item in targets:
        entry = {"path": item["path"], "ext": item["ext"], "url": "",
                 "uploaded": False, "detail": ""}

        if not client:
            entry["detail"] = "未配置平台参数（--platform-base / --platform-token），未做远端识别"
            pending.append(_pending(entry, item, results))
            continue

        # 扫描件 PDF 平台能直接解析成正文；图片只能上传后交给下游的视觉模型
        if item["ext"] == "pdf":
            try:
                text = client.parse(item["path"].rsplit("/", 1)[-1], item["data"])[:args.max_chars]
                results.append({
                    "path": item["path"], "ext": item["ext"], "kind": "document",
                    "ocr": True, "chars": len(text), "truncated": False, "text": text
                })
                log(f"平台解析 {item['path']}：{len(text)} 字")
                continue
            except platform_api.PlatformError as exc:
                entry["detail"] = f"{exc.kind}: {exc.detail}"
                pending.append(_pending(entry, item, results))
                continue

        try:
            entry["url"] = client.upload(item["path"].rsplit("/", 1)[-1], item["data"], image=True)
            entry["uploaded"] = True
            entry["detail"] = "已上传，待下游视觉模型识别"
        except platform_api.PlatformError as exc:
            entry["detail"] = f"{exc.kind}: {exc.detail}"
        pending.append(_pending(entry, item, results))
    return pending


def _pending(entry: dict, item: dict, results: list[dict]) -> dict:
    """无文本层 PDF 走到这一步说明识别没成，把文本层那点残留放回 files，别让材料整份消失。

    残留是空的就不要放——files 里出现 0 字的条目，看起来像「读到了但没内容」，
    比不出现更误导人。
    """
    fallback = item.get("_fallback")
    if fallback and fallback.get("chars"):
        results.append(fallback)
        entry["detail"] = f"{entry['detail']}；已回退到文本层抽取结果"
    return entry


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
    parser.add_argument("--platform-base", help="平台域名，用于把本地识别不了的材料交给平台")
    parser.add_argument("--platform-token", help="平台 Bearer token，只用于请求头，不写进输出")
    parser.add_argument("--parse-remote", choices=["auto", "off", "force"], default="auto",
                        help="auto=只有本地处理不了的材料才走平台；force=文档也交平台解析；off=完全不走平台")
    parser.add_argument("--check-env", action="store_true", help="探测依赖可用性后退出")
    args = parser.parse_args()

    if args.check_env:
        sys.exit(check_env(SCRIPTS_DIR))
    if bool(args.url) == bool(args.path):
        parser.error("--url 与 --path 二选一")
    if bool(args.platform_base) != bool(args.platform_token):
        parser.error("--platform-base 与 --platform-token 必须同时给出")
    if args.parse_remote == "off":
        args.platform_base = args.platform_token = None

    try:
        payload = build(args)
    except FatalError as exc:
        print(json.dumps({"error": {"kind": exc.kind, "detail": exc.detail}}, ensure_ascii=False))
        log(f"失败: {exc.kind} {exc.detail}")
        sys.exit(1)

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
