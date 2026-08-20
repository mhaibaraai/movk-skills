#!/usr/bin/env python3
# /// script
# dependencies = ["rapidocr-onnxruntime>=1.4,<2", "pypdfium2>=4", "numpy<3"]
# ///
"""
图片与无文本层 PDF 的 OCR，独立入口脚本。

**刻意不与 extract.py 共用依赖块**：OCR 这条链要装 onnxruntime + opencv 约 150MB，
沙箱每轮全新会重复付这个成本。做成单独入口后，extract.py --ocr off 的运行完全不碰它。
extract.py 只在真的遇到图片时才把本脚本作为子进程拉起来。

CLI:
  uv run scripts/ocr.py --paths '["/tmp/x/会议纪要1.png", "/tmp/x/扫描件.pdf"]'
  uv run scripts/ocr.py --check-env

输出 JSON: {"results": [{path, text, chars} | {path, error: {kind, detail}}]}
退出码: 0 正常；3 OCR 依赖不可用（rapidocr / opencv 装不上或缺 libGL.so.1）；2 参数错误。

模型权重打包在 rapidocr-onnxruntime 的 wheel 内，运行时不再联网拉模型——这正是选它
而不选新版 rapidocr 3.x 的原因：沙箱每轮全新，运行时下载模型多一个必失败点。
"""
import argparse
import json
import sys

# 低于该置信度的识别结果丢弃：会议纪要截图里的印章、手写签名常被识成随机字符
MIN_SCORE = 0.3
EXIT_UNAVAILABLE = 3

_engine = None


def probe() -> dict:
    """探测 OCR 链路是否可用。装不上或缺系统库时如实报出，不静默降级。"""
    status: dict[str, object] = {"rapidocr": False, "pypdfium2": False}
    try:
        import rapidocr_onnxruntime  # noqa: F401
        status["rapidocr"] = True
    except Exception as exc:
        status["rapidocr_detail"] = f"{type(exc).__name__}: {exc}"
    try:
        import pypdfium2  # noqa: F401
        status["pypdfium2"] = True
    except Exception as exc:
        status["pypdfium2_detail"] = f"{type(exc).__name__}: {exc}"
    return status


def get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    return _engine


def _run(image) -> str:
    result, _elapse = get_engine()(image)
    if not result:
        return ""
    lines = [str(text).strip() for _box, text, score in result
             if float(score) >= MIN_SCORE and str(text).strip()]
    return "\n".join(lines)


def ocr_image(path: str) -> str:
    return _run(path)


def ocr_pdf(path: str, max_pages: int, scale: float) -> str:
    import numpy as np
    import pypdfium2 as pdfium

    pages: list[str] = []
    document = pdfium.PdfDocument(path)
    try:
        for index in range(min(len(document), max_pages)):
            bitmap = document[index].render(scale=scale)
            text = _run(np.asarray(bitmap.to_pil().convert("RGB")))
            if text:
                pages.append(text)
    finally:
        document.close()
    return "\n".join(pages)


def main() -> None:
    parser = argparse.ArgumentParser(description="图片 / 扫描件 PDF 的 OCR")
    parser.add_argument("--paths", help="JSON 字符串数组，待识别的本地文件路径")
    parser.add_argument("--max-pages", type=int, default=10, help="PDF 最多栅格化的页数")
    parser.add_argument("--scale", type=float, default=2.0, help="PDF 栅格化倍率，越大越慢越准")
    parser.add_argument("--check-env", action="store_true", help="探测 OCR 依赖可用性后退出")
    args = parser.parse_args()

    if args.check_env:
        status = probe()
        print(json.dumps(status, ensure_ascii=False, indent=2))
        ok = bool(status["rapidocr"]) and bool(status["pypdfium2"])
        if not ok:
            print("OCR 依赖不可用：图片材料将降级为仅列文件名。"
                  "常见原因是精简镜像缺 libGL.so.1（opencv-python 的系统依赖）。", file=sys.stderr)
        sys.exit(0 if ok else EXIT_UNAVAILABLE)

    if not args.paths:
        parser.error("需要 --paths 或 --check-env")

    try:
        paths = json.loads(args.paths)
        assert isinstance(paths, list)
    except Exception:
        parser.error("--paths 必须是 JSON 字符串数组")

    status = probe()
    if not status["rapidocr"]:
        print(json.dumps({"results": [], "unavailable": status}, ensure_ascii=False))
        sys.exit(EXIT_UNAVAILABLE)

    results = []
    for path in paths:
        try:
            if str(path).lower().endswith(".pdf"):
                if not status["pypdfium2"]:
                    raise RuntimeError("pypdfium2 不可用，无法栅格化 PDF")
                text = ocr_pdf(path, args.max_pages, args.scale)
            else:
                text = ocr_image(path)
            results.append({"path": path, "text": text, "chars": len(text)})
        except Exception as exc:
            results.append({
                "path": path,
                "error": {"kind": "ocr_failed", "detail": f"{type(exc).__name__}: {exc}"}
            })
        print(f"[ocr] {path}", file=sys.stderr)

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
