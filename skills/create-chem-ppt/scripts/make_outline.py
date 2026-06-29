#!/usr/bin/env python3
"""按「主题 + 总页数」自动分配章节，生成大纲骨架 JSON。

用法：
    python scripts/make_outline.py --template 安全生产培训 --pages 12 \
        --title "动火作业安全" --sections "风险辨识,作业票证,监护要求,应急处置" > outline.json

生成的骨架含封面/目录/分隔/要点/封底占位，由模型/人工填充正文，再交给 build_pptx.py。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THEMES = json.loads((ROOT / "templates" / "themes.json").read_text(encoding="utf-8"))


def allocate(total: int, sections: list[str]) -> list[dict]:
    """固定开销：封面+目录+封底=3。每节 1 分隔页，剩余按节均分内容页。"""
    pages = [
        {"type": "cover", "title": "{标题}", "subtitle": "{副标题}", "org": "中国石油化工集团有限公司"},
        {"type": "toc", "items": list(sections)},
    ]
    body = max(total - 3 - len(sections), len(sections))
    per = max(1, body // len(sections))
    for sec in sections:
        pages.append({"type": "section", "title": sec, "subtitle": "{要点}"})
        for _ in range(per):
            pages.append({"type": "points", "title": "{小标题}",
                          "points": [{"head": "{要点}", "body": "{说明}"}]})
    pages.append({"type": "closing", "title": "谢 谢", "org": "中国石油化工集团有限公司"})
    return pages[:total] if len(pages) > total else pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True, choices=list(THEMES))
    ap.add_argument("--pages", type=int, default=12)
    ap.add_argument("--title", default="{标题}")
    ap.add_argument("--sections", default="")
    a = ap.parse_args()
    secs = [s.strip() for s in a.sections.split(",") if s.strip()] or THEMES[a.template]["section_words"]
    data = {"template": a.template, "title": a.title, "pages": allocate(a.pages, secs)}
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
