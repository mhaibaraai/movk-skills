#!/usr/bin/env python3
"""生成大纲骨架，或把大纲渲染成 Markdown 分页预览。纯标准库。

用法：
    uv run scripts/make_outline.py --template 员工安全知识培训 --pages 14 \
        --title "动火作业安全培训" --sections "风险辨识,作业票证,监护要求,应急处置" > outline.json
    uv run scripts/make_outline.py --preview outline.json

骨架里的每一页都已绑定模板真实页（src）与该页的要点容量（items 数），
模型只负责把 {占位} 换成真实文案——不要增删要点条数，多一条就会溢出版式。
"""
from __future__ import annotations

import argparse
import json
import sys
from itertools import cycle
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pptx_util import load_index, load_registry, resolve_template  # noqa: E402

KIND_LABEL = {
    "cover": "封面",
    "toc": "目录",
    "section": "章节",
    "content": "要点",
    "table": "表格",
    "closing": "封底",
}
MAX_ITEMS = 6  # 单页要点上限，超过的模板页（时间轴/密集图表）不参与排布


def pick(slides: list[dict], kind: str) -> list[dict]:
    return [s for s in slides if s["kind"] == kind]


def content_pool(slides: list[dict]) -> list[dict]:
    """可用内容页。优先 2 条以上要点的版式——单要点页信息量太薄，只在没得选时才用。"""
    usable = [s for s in pick(slides, "content") if 1 <= s.get("items", 0) <= MAX_ITEMS]
    rich = [s for s in usable if s["items"] >= 2]
    return sorted(rich or usable, key=lambda s: s["items"])


def caps_of(slide: dict) -> dict[str, int]:
    """该页每类槽位的字数上限，取同角色里最紧的那个——写作时必须按最紧的来。"""
    caps: dict[str, int] = {}
    for slot in slide["slots"]:
        if "cap" in slot:
            role = slot["role"]
            caps[role] = min(caps.get(role, 999), slot["cap"])
    return caps


def hints(slide: dict) -> dict[int, str]:
    """封面/封底槽位的模板原文，用作占位提示——不给提示，模型不知道这里该填汇报人还是日期。"""
    return {
        slot["idx"]: slot["sample"]
        for slot in slide["slots"]
        if slot["role"] == "item_body" and "idx" in slot
    }


def blank_page(slide: dict, title: str) -> dict:
    kind = slide["kind"]
    page = {"src": slide["i"], "kind": kind, "title": title}
    caps = caps_of(slide)
    if caps:
        page["caps"] = caps
    items = slide.get("items", 0)
    if not items:
        return page
    if kind == "toc":
        page["items"] = [{"head": "{章节名}"} for _ in range(items)]
    elif kind in ("cover", "closing"):
        sample = hints(slide)
        page["items"] = [{"body": "{%s}" % sample.get(i, "署名")} for i in range(items)]
    else:
        page["items"] = [{"head": "{要点}", "body": "{说明}"} for _ in range(items)]
    return page


def warn_too_long(pages: list[dict]) -> None:
    """自动填入的标题/章节名超出槽位容量时告警——溢出会把整页版式压垮。"""
    for n, page in enumerate(pages, 1):
        cap = page.get("caps", {}).get("title")
        title = page.get("title", "")
        if cap and not title.startswith("{") and len(title) > cap:
            print(
                f"警告：第 {n} 页标题「{title}」{len(title)} 字，超出该版式上限 {cap} 字。"
                f"请改短（长名称放副标题），否则会溢出版式。",
                file=sys.stderr,
            )


def allocate(index: dict, total: int, title: str, sections: list[str]) -> list[dict]:
    """封面 + 目录 + 各章（分隔页 + 若干内容页）+ 封底，正文页在章节间均分。"""
    slides = index["slides"]
    cover, toc = pick(slides, "cover")[0], pick(slides, "toc")
    closing = pick(slides, "closing")
    section_pages = pick(slides, "section")
    pool = content_pool(slides)
    if not pool:
        raise SystemExit(f"模板 {index['template']} 没有可用内容页")

    pages = [blank_page(cover, title) | {"subtitle": "{副标题}"}]
    fixed = 1 + len(closing)
    if toc:
        toc_page = min(toc, key=lambda s: abs(s.get("items", 0) - len(sections)))
        page = blank_page(toc_page, "目录")
        page["items"] = [{"head": s} for s in sections[: toc_page.get("items", len(sections))]]
        pages.append(page)
        fixed += 1

    n = len(sections)
    if section_pages:
        fixed += n
    body_total = max(total - fixed, n)
    base, extra = divmod(body_total, n)  # 余数摊给前几章，避免总页数少于用户要求
    sections_cycle = cycle(section_pages) if section_pages else None
    taken = 0

    for i, name in enumerate(sections):
        if sections_cycle:
            pages.append(blank_page(next(sections_cycle), name))
        # 优先选页眉放得下这个章节名的版式，放不下再退回全量
        fit = [s for s in pool if caps_of(s).get("title", 99) >= len(name)] or pool
        for _ in range(max(1, base + (1 if i < extra else 0))):
            pages.append(blank_page(fit[taken % len(fit)], name))
            taken += 1

    if closing:
        pages.append(blank_page(closing[0], "{致谢}"))
    pages = pages[:total] if len(pages) > total else pages
    warn_too_long(pages)
    return pages


def preview(outline: dict) -> str:
    """分页 Markdown 卡片：每页一块，供确认门直接渲染，用户按页核对。"""
    pages = outline["pages"]
    lines = [f"**{outline['title']}**　模板：{outline['template']}　共 {len(pages)} 页"]
    section_no = 0
    for n, page in enumerate(pages, 1):
        kind = page["kind"]
        label = KIND_LABEL.get(kind, kind)
        title = page.get("title", "")
        items = page.get("items", [])
        lines.append("\n---\n")
        if kind == "section":
            section_no += 1
            lines.append(f"**第 {n} 页**　`{label}`")
            lines.append(f"### {section_no:02d} · {title}")
            continue
        if kind == "cover":
            lines.append(f"**第 {n} 页**　`{label}`")
            lines.append(f"# {title}")
            if page.get("subtitle"):
                lines.append(page["subtitle"])
            lines += [f"　{it['body']}" for it in items if it.get("body")]
            continue
        if kind == "closing":
            lines.append(f"**第 {n} 页**　`{label}`")
            lines.append(f"# {title}")
            if page.get("subtitle"):
                lines.append(page["subtitle"])
            continue
        head = f"**第 {n} 页**　`{label}`"
        if title:
            head += f"　{title}"
        lines.append(head)
        if page.get("subtitle"):
            lines.append(f"*{page['subtitle']}*")
        if kind == "toc":
            lines += [f"- {it['head']}" for it in items if it.get("head")]
        else:
            for it in items:
                h, b = it.get("head", ""), it.get("body", "")
                if h and b:
                    lines.append(f"- **{h}** — {b}")
                elif h:
                    lines.append(f"- **{h}**")
                elif b:
                    lines.append(f"- {b}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", metavar="OUTLINE", help="把大纲渲染成 Markdown 预览")
    parser.add_argument("--template")
    parser.add_argument("--pages", type=int, default=14)
    parser.add_argument("--title", default="{标题}")
    parser.add_argument("--sections", default="")
    args = parser.parse_args()

    if args.preview:
        print(preview(json.loads(Path(args.preview).read_text(encoding="utf-8"))))
        return
    if not args.template:
        raise SystemExit(f"--template 必填。可选：{'、'.join(load_registry())}")

    key, item = resolve_template(args.template)
    sections = [s.strip() for s in args.sections.split(",") if s.strip()] or item["sections"]
    outline = {
        "template": key,
        "title": args.title,
        "pages": allocate(load_index(key), args.pages, args.title, sections),
    }
    print(json.dumps(outline, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
