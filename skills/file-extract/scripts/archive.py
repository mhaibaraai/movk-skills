#!/usr/bin/env python3
# /// script
# dependencies = []
# ///
"""
压缩包解包与条目分类。只用标准库，不碰磁盘——条目全部以字节留在内存里，
由调用方决定是否落盘（extract.py --out-dir）。

对外接口：
  unpack(data, name, ...)   -> (files, skipped, errors)
  classify(path)            -> ("document" | "image" | "archive" | "", ext)
  decode_name(info)         -> 修复编码后的条目路径
  is_noise(path)            -> 是否为系统噪音文件

files 每项 {path, ext, kind, data}；skipped 每项 {path, reason}；errors 每项 {path, kind, detail}。
"""
import io
import posixpath
import zipfile

DOCUMENT_EXTS = {"pdf", "docx", "txt", "md", "csv"}
IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff"}
ARCHIVE_EXTS = {"zip"}

# 默认上限：解压后累计字节、条目数、嵌套深度。三道一起挡 zip bomb——
# 只看压缩包体积挡不住，1MB 的包能解出几个 GB。
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_ENTRIES = 500
MAX_DEPTH = 2

_NOISE_BASENAMES = {".ds_store", "thumbs.db", "desktop.ini"}


def ext_of(path: str) -> str:
    base = posixpath.basename(path)
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def classify(path: str) -> tuple[str, str]:
    ext = ext_of(path)
    if ext in DOCUMENT_EXTS:
        return "document", ext
    if ext in IMAGE_EXTS:
        return "image", ext
    if ext in ARCHIVE_EXTS:
        return "archive", ext
    return "", ext


def is_noise(path: str) -> bool:
    segments = [s for s in path.split("/") if s]
    if any(s == "__MACOSX" for s in segments):
        return True
    base = segments[-1] if segments else ""
    if base.lower() in _NOISE_BASENAMES:
        return True
    # ._xxx 是 macOS 的资源分叉，~$xxx 是 Office 打开文档时的锁文件，都不是用户材料
    return base.startswith("._") or base.startswith("~$") or base.startswith(".")


def decode_name(info: zipfile.ZipInfo) -> str:
    """修复中文文件名乱码。

    zip 条目未置 UTF-8 标志位时 zipfile 一律按 cp437 解码，Windows / 部分打包工具
    写进去的其实是 GBK 字节，直接用会得到一串乱码路径。这里把 cp437 解码逆回原始
    字节再重猜编码；先试 utf-8（严格模式下 GBK 字节几乎必然失败），再试 gbk。
    """
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return info.filename


def is_zip(data: bytes) -> bool:
    return data[:2] == b"PK"


# docx / xlsx / pptx 本身就是 zip，直接按压缩包展开会把 word/document.xml 这些内部零件
# 当成材料吐出来。OOXML 容器都带 [Content_Types].xml，据此把它们挡在解包之外。
OOXML_MARKER = "[Content_Types].xml"


def is_ooxml(data: bytes) -> bool:
    if not is_zip(data):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return OOXML_MARKER in archive.namelist()
    except zipfile.BadZipFile:
        return False


def unpack(
    data: bytes,
    name: str = "",
    *,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_entries: int = MAX_ENTRIES,
    depth: int = 0,
    prefix: str = "",
) -> tuple[list[dict], list[dict], list[dict]]:
    """解包一份字节。不是压缩包（或本身就是 Office 文档）时按单文件处理，是压缩包时递归展开。"""
    if not is_zip(data) or is_ooxml(data):
        return _single_file(data, prefix or name)

    files: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        if depth == 0:
            raise
        errors.append({"path": prefix or name, "kind": "bad_archive", "detail": str(exc)})
        return files, skipped, errors

    total = 0
    with zf:
        for info in zf.infolist():
            path = posixpath.join(prefix, decode_name(info)) if prefix else decode_name(info)
            if info.is_dir():
                continue
            if is_noise(path):
                skipped.append({"path": path, "reason": "system_noise"})
                continue
            if len(files) >= max_entries:
                skipped.append({"path": path, "reason": "entry_limit"})
                continue

            kind, ext = classify(path)
            if not kind:
                errors.append({
                    "path": path,
                    "kind": "unsupported_ext",
                    "detail": f"不支持的扩展名 .{ext}" if ext else "无扩展名，无法判断类型"
                })
                continue

            total += info.file_size
            if total > max_total_bytes:
                errors.append({
                    "path": path,
                    "kind": "too_large",
                    "detail": f"解压后累计超过 {max_total_bytes // 1024 // 1024}MB，剩余条目未处理"
                })
                break

            try:
                payload = zf.read(info)
            except Exception as exc:
                errors.append({"path": path, "kind": "parse_failed", "detail": f"读取条目失败: {exc}"})
                continue

            if kind == "archive":
                if depth + 1 > MAX_DEPTH:
                    skipped.append({"path": path, "reason": "nesting_limit"})
                    continue
                sub = unpack(
                    payload, path,
                    max_total_bytes=max_total_bytes - total,
                    max_entries=max_entries - len(files),
                    depth=depth + 1,
                    prefix=path,
                )
                files.extend(sub[0])
                skipped.extend(sub[1])
                errors.extend(sub[2])
                continue

            files.append({"path": path, "ext": ext, "kind": kind, "data": payload})

    return files, skipped, errors


def _single_file(data: bytes, name: str) -> tuple[list[dict], list[dict], list[dict]]:
    kind, ext = classify(name)
    if kind == "archive":
        # 叫 .zip 却没有 PK 魔数：多半是下载到了错误页或文件损坏，别当单文件蒙混过去
        return [], [], [{"path": name, "kind": "bad_archive", "detail": "扩展名是压缩包但内容不是有效 zip"}]
    if not kind:
        detail = f"不支持的扩展名 .{ext}" if ext else "无扩展名，无法判断类型"
        return [], [], [{"path": name, "kind": "unsupported_ext", "detail": detail}]
    return [{"path": name, "ext": ext, "kind": kind, "data": data}], [], []
