#!/usr/bin/env python3
# /// script
# dependencies = ["Pillow>=10.1", "pypdfium2>=4"]
# ///
"""把 pptx 逐页渲染成缩略图，并拼成带页码的网格图——一图两用：

- 选版式：对模板 pptx 渲染，让用户/模型看清每页真实版面再定制。
- 成稿预览/QA：对最终 pptx 渲染，肉眼查文本溢出、元素重叠、装饰错位（anthropics QA 清单）。

管线：soffice 把 pptx 转 pdf（唯一保真渲染器）→ pypdfium2 栅格化每页为 PNG（自包含 wheel，
无需系统 poppler）→ Pillow 合成网格。渲染器必须是系统预装的 LibreOffice；uv 只能装 Python 包、
装不了 soffice，因此沙箱无 soffice 时本脚本以退出码 3 优雅失败，交由上层降级为「文本卡片 + 可下载 pptx」。

用法：
    uv run skills/create-ppt/scripts/thumbnail.py deck.pptx --out-dir thumbs
    uv run skills/create-ppt/scripts/thumbnail.py deck.pptx --out-dir thumbs --dpi 120 --cols 3
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NO_RENDERER = 3  # 约定退出码：无 soffice，上层据此降级

SOFFICE_CANDIDATES = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/opt/libreoffice/program/soffice",
)


def find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        if path := shutil.which(name):
            return path
    return next((p for p in SOFFICE_CANDIDATES if Path(p).exists()), None)


def pptx_to_pdf(soffice: str, deck: Path, workdir: Path) -> Path:
    """soffice 无头转 pdf。用独立 UserInstallation profile，避免与已运行的 LibreOffice 抢锁。"""
    profile = (workdir / "lo_profile").as_uri()
    cmd = [
        soffice, "--headless", "--norestore", f"-env:UserInstallation={profile}",
        "--convert-to", "pdf", "--outdir", str(workdir), str(deck),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    pdf = workdir / f"{deck.stem}.pdf"
    if proc.returncode != 0 or not pdf.exists():
        raise SystemExit(
            f"soffice 转 pdf 失败（returncode={proc.returncode}）：{proc.stderr.strip()[:300]}"
        )
    return pdf


def pdf_to_pngs(pdf: Path, out_dir: Path, dpi: int, pages: list[int] | None):
    """pypdfium2 逐页栅格化为 PNG，返回 (页码1起, PNG 路径) 列表。"""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    total = len(doc)
    wanted = pages or list(range(1, total + 1))
    scale = dpi / 72.0
    produced = []
    for n in wanted:
        if not 1 <= n <= total:
            continue
        image = doc[n - 1].render(scale=scale).to_pil()
        path = out_dir / f"page-{n:02d}.png"
        image.save(path)
        produced.append((n, path))
    doc.close()
    return produced


def compose_grid(pages: list[tuple[int, Path]], out: Path, cols: int, thumb_w: int) -> None:
    """把逐页 PNG 缩放拼成网格，每格左上角标页码。

    标签用 ASCII「P1」而非「第 1 页」：Pillow 内置位图字体没有中日韩字形，沙箱通常也不带
    中文字体，写中文会渲染成一排方块，页码反而认不出来。
    """
    from PIL import Image, ImageDraw, ImageFont

    if not pages:
        return
    thumbs = []
    for n, path in pages:
        im = Image.open(path).convert("RGB")
        h = max(1, round(im.height * thumb_w / im.width))
        thumbs.append((n, im.resize((thumb_w, h))))

    pad, label_h = 12, 30
    cell_h = max(im.height for _, im in thumbs) + label_h
    rows = (len(thumbs) + cols - 1) // cols
    grid = Image.new(
        "RGB", (cols * thumb_w + (cols + 1) * pad, rows * cell_h + (rows + 1) * pad), "white"
    )
    draw = ImageDraw.Draw(grid)
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:  # 老 Pillow 的 load_default 不接受 size
        font = ImageFont.load_default()

    for i, (n, im) in enumerate(thumbs):
        r, c = divmod(i, cols)
        x = pad + c * (thumb_w + pad)
        y = pad + r * (cell_h + pad)
        draw.text((x, y), f"P{n}", fill="black", font=font)
        grid.paste(im, (x, y + label_h))
    grid.save(out)


def parse_pages(spec: str | None) -> list[int] | None:
    if not spec:
        return None
    return [int(x) for x in spec.replace("，", ",").split(",") if x.strip().isdigit()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("deck", help="待渲染的 pptx")
    parser.add_argument("--out-dir", default="thumbs", help="输出目录（默认 thumbs/）")
    parser.add_argument("--dpi", type=int, default=100, help="栅格化 dpi，QA 用 120–150")
    parser.add_argument("--cols", type=int, default=3, help="网格列数")
    parser.add_argument("--max-width", type=int, default=360, help="单页缩略图宽度 px")
    parser.add_argument("--pages", help="只渲染指定页（1 起，逗号分隔），默认全部")
    args = parser.parse_args()

    deck = Path(args.deck)
    if not deck.exists():
        raise SystemExit(f"文件不存在：{deck}")

    soffice = find_soffice()
    if not soffice:
        print(
            "未找到 soffice/LibreOffice：本环境无法渲染缩略图。"
            "请降级为「文本卡片预览 + 提供 pptx 下载链接由用户自行打开」。",
            file=sys.stderr,
        )
        sys.exit(NO_RENDERER)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        pdf = pptx_to_pdf(soffice, deck, Path(tmp))
        pages = pdf_to_pngs(pdf, out_dir, args.dpi, parse_pages(args.pages))
    if not pages:
        raise SystemExit("未渲染出任何页")

    grid = out_dir / "grid.png"
    compose_grid(pages, grid, max(1, args.cols), args.max_width)

    print(f"渲染 {len(pages)} 页缩略图 → {out_dir}/page-*.png")
    print(f"网格图：{grid}")


if __name__ == "__main__":
    main()
