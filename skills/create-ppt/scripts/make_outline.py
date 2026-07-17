#!/usr/bin/env python3
"""生成大纲骨架、渲染 Markdown 分页预览、从预览卡片重建大纲，或对大纲做定点改写。纯标准库。

用法：
    uv run scripts/make_outline.py --template 员工安全知识培训 --pages 14 \
        --title "动火作业安全培训" --sections "风险辨识,作业票证,监护要求,应急处置" > outline.json
    uv run scripts/make_outline.py --preview outline.json
    uv run scripts/make_outline.py --patch outline.json \
        --ops '[{"page":4,"field":"items[0].body","value":"作业前完成气体检测"}]'
    uv run scripts/make_outline.py --from-preview card.md --out outline.json

骨架里的每一页都已绑定模板真实页（src）与该页的要点容量（items 数），
模型只负责把 {占位} 换成真实文案——不要增删要点条数，多一条就会溢出版式。
门② 修改用 --patch 定点改写 outline.json（结构不变，只换文本），不必重出完整大纲。

分页卡片是正式的序列化格式：--preview 的输出文法即 --from-preview 的解析文法。
outline.json 丢失时（如跨轮沙箱失效、跨节点交接），把最近一轮卡片文本喂给
--from-preview 即可无损重建——src/caps 由确定性重排还原，文本从卡片回填。
"""
from __future__ import annotations

import argparse
import json
import re
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
    """分页 Markdown 卡片：每页一块，供确认门直接渲染，用户按页核对。

    卡片同时是大纲的无损序列化：文本槽全部在卡片里（src/caps 可确定性重排还原），
    末尾以分隔线终止，卡片后追加的引导语/标记不会污染最后一页的解析。
    改动此处的输出文法时，必须同步 parse_page 的解析文法。
    """
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
        if kind in ("cover", "closing"):
            lines.append(f"**第 {n} 页**　`{label}`")
            lines.append(f"# {title}")
            if page.get("subtitle"):
                lines.append(page["subtitle"])
            lines += [f"- {it['body']}" for it in items if it.get("body")]
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
    lines.append("\n---\n")
    return "\n".join(lines)


LABEL_KIND = {v: k for k, v in KIND_LABEL.items()}
# 分隔位写成 [　 ]+：preview 出的是全角空格，但卡片经平台变量传递会被归一化成半角，
# 两种都要认——只认全角会让每一次跨节点重建都失败。捕获组一律 strip，避免归一化
# 带进的前导空格混进标题（" 总体情况" 会平白多占一个 cap 字符）。
SEP = r"[　 ]+"
CARD_HEAD_RE = re.compile(rf"^\*\*(.+?)\*\*{SEP}模板：(.+?){SEP}共 (\d+) 页$")
CARD_PAGE_RE = re.compile(rf"^\*\*第 \d+ 页\*\*{SEP}`([^`]+)`(?:{SEP}(.*))?$")
CARD_SECTION_RE = re.compile(r"^### \d+ · (.+)$")
CARD_ITEM_RE = re.compile(r"^- \*\*(.+?)\*\*(?: — (.*))?$")


def parse_page(block: list[str]) -> dict | None:
    """解析一个卡片块。首行不是页头说明卡片已结束（引导语/变更摘要），返回 None。"""
    head = CARD_PAGE_RE.match(block[0])
    if not head:
        return None
    label = head.group(1).strip()
    kind = LABEL_KIND.get(label)
    if kind is None:
        raise SystemExit(f"--from-preview: 未知页型标签 `{label}`")
    page = {"kind": kind, "title": (head.group(2) or "").strip(), "subtitle": "", "items": []}
    for line in block[1:]:
        if kind == "section":
            if m := CARD_SECTION_RE.match(line):
                page["title"] = m.group(1).strip()
        elif kind in ("cover", "closing"):
            if line.startswith("# "):
                page["title"] = line[2:].strip()
            elif line.startswith("- "):
                page["items"].append({"body": line[2:].strip()})
            elif not page["subtitle"]:
                page["subtitle"] = line.strip()
        elif kind == "toc":
            if line.startswith("- "):
                page["items"].append({"head": line[2:].strip()})
        else:
            if m := CARD_ITEM_RE.match(line):
                page["items"].append({"head": m.group(1).strip(), "body": (m.group(2) or "").strip()})
            elif line.startswith("- "):
                page["items"].append({"head": "", "body": line[2:].strip()})
            elif len(line) > 2 and line.startswith("*") and line.endswith("*"):
                page["subtitle"] = line[1:-1].strip()
    if kind == "section" and not page["title"]:
        raise SystemExit("--from-preview: 章节页缺少「### 序号 · 标题」行")
    return page


def parse_preview(text: str) -> dict:
    """把分页卡片文本解析回 {template, title, pages}（pages 仅含页型与文本，无 src/caps）。"""
    lines = [line.rstrip() for line in text.splitlines()]
    for start, line in enumerate(lines):
        if head := CARD_HEAD_RE.match(line):
            break
    else:
        raise SystemExit(
            "--from-preview: 找不到卡片头行（**标题**　模板：X　共 N 页）。"
            "卡片必须是 --preview 的原样输出，不能手写或补写头行；"
            "没有真卡片时请重跑 make_outline.py 生成骨架、填充 outline.json 后用 --preview 产出"
        )
    title, template, total = head.group(1).strip(), head.group(2).strip(), int(head.group(3))
    pages: list[dict] = []
    block: list[str] = []
    for line in lines[start + 1 :] + ["---"]:
        if line.strip() == "---":
            if block:
                page = parse_page(block)
                if page is None:
                    break
                pages.append(page)
                block = []
            continue
        if line:
            block.append(line)
    if not pages:
        raise SystemExit(
            "--from-preview: 正文没有任何「**第 N 页**　`页型`」页头——"
            "这不是 --preview 的原样输出（疑似手写或改写的大纲）。"
            "卡片不能手写或修补：请重跑 make_outline.py 生成骨架、"
            "填充 outline.json 后用 --preview 产出卡片再重试"
        )
    if len(pages) != total:
        raise SystemExit(
            f"--from-preview: 头行声明共 {total} 页，实际解析到 {len(pages)} 页。"
            "卡片可能被截断或改写：请回到 --preview 的原样输出重试，不要手工修补"
        )
    return {"template": template, "title": title, "pages": pages}


def align_page(sk: dict, pv: dict, n: int, index: dict, pool: list[dict], picked: dict[int, int]) -> dict:
    """让骨架页与卡片页对齐：页型必须一致；条数不一致时确定性换选同容量的模板页。"""
    if sk["kind"] != pv["kind"]:
        raise SystemExit(f"--from-preview: 第 {n} 页页型对不上（重排为 {sk['kind']}，卡片是 {pv['kind']}）")
    need, have = len(pv["items"]), len(sk.get("items", []))
    if need == have:
        return sk
    if sk["kind"] == "content":
        fit = [s for s in pool if s.get("items", 0) == need]
        fit = [s for s in fit if caps_of(s).get("title", 99) >= len(pv["title"])] or fit
        if fit:
            k = picked.get(need, 0)
            picked[need] = k + 1
            return blank_page(fit[k % len(fit)], pv["title"])
        if need < have:
            return sk  # 空余槽位留空，preview 会自然略过
        raise SystemExit(f"--from-preview: 第 {n} 页有 {need} 条要点，模板没有能容纳的内容页")
    if sk["kind"] == "toc":
        toc = min(pick(index["slides"], "toc"), key=lambda s: abs(s.get("items", 0) - need))
        page = blank_page(toc, "目录")
        if len(page.get("items", [])) < need:
            raise SystemExit(f"--from-preview: 目录 {need} 条超出模板目录页容量")
        return page
    if need > have:
        raise SystemExit(f"--from-preview: 第 {n} 页（{sk['kind']}）有 {need} 条条目，超出模板该页容量 {have}")
    return sk


def fill_page(page: dict, pv: dict, n: int) -> dict:
    """把卡片文本回填进骨架页：只写文本槽，src/caps/条数保持骨架原样。"""
    page["title"] = pv["title"]
    if "subtitle" in page or pv["subtitle"]:
        page["subtitle"] = pv["subtitle"]
    slots = page.get("items", [])
    if len(pv["items"]) > len(slots):
        raise SystemExit(f"--from-preview: 第 {n} 页条目多于版式槽位（{len(pv['items'])} > {len(slots)}）")
    for i, slot in enumerate(slots):
        parsed_item = pv["items"][i] if i < len(pv["items"]) else {}
        for field in slot:
            slot[field] = parsed_item.get(field, "")
    return page


def warn_caps(pages: list[dict]) -> None:
    """全量 caps 核对，超限走 stderr 告警（与 patch 同款，不阻断）。"""
    for n, page in enumerate(pages, 1):
        caps = page.get("caps", {})
        checks = [("title", page.get("title", "")), ("subtitle", page.get("subtitle", ""))]
        for it in page.get("items", []):
            checks += [("item_head", it.get("head", "")), ("item_body", it.get("body", ""))]
        for role, text in checks:
            cap = caps.get(role)
            if cap and text and not text.startswith("{") and len(text) > cap:
                print(
                    f"警告：第 {n} 页 {role}「{text}」{len(text)} 字，超出该版式上限 {cap} 字。"
                    f"请改短，否则会溢出版式。",
                    file=sys.stderr,
                )


def rebuild(parsed: dict) -> dict:
    """卡片 → outline：allocate 是 (模板, 页数, 标题, 章节) 的确定性函数，
    从卡片解析出这四个参数重排即可还原 src/caps 骨架，再逐页回填文本。"""
    key, _ = resolve_template(parsed["template"])
    index = load_index(key)
    pages = parsed["pages"]
    sections = [p["title"] for p in pages if p["kind"] == "section"]
    if not sections:  # 模板无章节分隔页时，从内容页页眉按序去重兜底
        sections = list(dict.fromkeys(p["title"] for p in pages if p["kind"] == "content" and p["title"]))
    if not sections:
        raise SystemExit("--from-preview: 卡片里没有章节页，也无法从内容页页眉推出章节")
    skeleton = allocate(index, len(pages), parsed["title"], sections)
    if len(skeleton) != len(pages):
        raise SystemExit(f"--from-preview: 按卡片参数重排出 {len(skeleton)} 页，与卡片 {len(pages)} 页对不上")
    pool = content_pool(index["slides"])
    picked: dict[int, int] = {}
    rebuilt = [
        fill_page(align_page(sk, pv, n, index, pool, picked), pv, n)
        for n, (sk, pv) in enumerate(zip(skeleton, pages), 1)
    ]
    return {"template": key, "title": parsed["title"], "pages": rebuilt}


def from_preview(text: str) -> dict:
    """从卡片文本重建 outline，并以「重建结果的预览可再解析回同一结构」做无损自检。"""
    parsed = parse_preview(text)
    outline = rebuild(parsed)
    if parse_preview(preview(outline)) != parsed:
        raise SystemExit("--from-preview: 重建结果的预览与输入卡片不一致，卡片可能被改坏")
    warn_caps(outline["pages"])
    return outline


FIELD_RE = re.compile(r"^items\[(\d+)\]\.(head|body)$")
CAP_ROLE = {"title": "title", "subtitle": "subtitle", "head": "item_head", "body": "item_body"}


def apply_op(pages: list[dict], op: dict) -> tuple[int, str, str]:
    """把一条定点改写落到目标页字段。只换文本，不动 src/kind/caps/items 条数。"""
    page_no, field, value = op.get("page"), op.get("field"), op.get("value")
    if not isinstance(page_no, int) or not 1 <= page_no <= len(pages):
        raise SystemExit(f"--patch: page 越界或非法：{page_no!r}（共 {len(pages)} 页）")
    if not isinstance(value, str):
        raise SystemExit(f"--patch: value 必须是字符串：{value!r}")
    page = pages[page_no - 1]
    if field in ("title", "subtitle"):
        page[field] = value
        return page_no, field, value
    match = FIELD_RE.match(field or "")
    if not match:
        raise SystemExit(
            f"--patch: 非法字段 {field!r}，仅支持 title / subtitle / items[N].head / items[N].body"
        )
    idx, sub = int(match.group(1)), match.group(2)
    items = page.get("items", [])
    if idx >= len(items):
        raise SystemExit(
            f"--patch: 第 {page_no} 页要点序号 {idx} 越界（该页 {len(items)} 条，不可增删，"
            f"要更多要点请改页数重跑 make_outline）"
        )
    items[idx][sub] = value
    return page_no, CAP_ROLE[sub], value


def patch(outline: dict, ops: list[dict]) -> dict:
    """按 ops 定点改写大纲，超 caps 的字段走 stderr 告警（不阻断，与 warn_too_long 同款）。"""
    pages = outline["pages"]
    for op in ops:
        page_no, cap_role, value = apply_op(pages, op)
        cap = pages[page_no - 1].get("caps", {}).get(cap_role)
        if cap and len(value) > cap:
            print(
                f"警告：第 {page_no} 页 {cap_role}「{value}」{len(value)} 字，超出该版式上限 {cap} 字。"
                f"请改短，否则会溢出版式。",
                file=sys.stderr,
            )
    return outline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", metavar="OUTLINE", help="把大纲渲染成 Markdown 预览")
    parser.add_argument("--patch", metavar="OUTLINE", help="对大纲做定点文本改写（原地回写）")
    parser.add_argument("--ops", help="--patch 的操作数组（JSON），每项 {page, field, value}")
    parser.add_argument("--from-preview", metavar="CARD", help="从分页卡片文本重建大纲（- 读 stdin）")
    parser.add_argument("--out", metavar="OUTLINE", help="--from-preview 的落盘路径，缺省打印 stdout")
    parser.add_argument("--template")
    parser.add_argument("--pages", type=int, default=14)
    parser.add_argument("--title", default="{标题}")
    parser.add_argument("--sections", default="")
    args = parser.parse_args()

    if args.preview:
        print(preview(json.loads(Path(args.preview).read_text(encoding="utf-8"))))
        # 走 stderr：提醒只给模型看，不进 stdout，才不会被复制进卡片污染 --from-preview
        print(
            "提醒：以上卡片必须原样粘贴进你的回复正文。工具执行的 stdout 只有你自己看得见，"
            "用户与下游节点只能读到你的回复正文——跑过 --preview 不等于回复里有卡片。",
            file=sys.stderr,
        )
        return
    if args.from_preview:
        text = (
            sys.stdin.read()
            if args.from_preview == "-"
            else Path(args.from_preview).read_text(encoding="utf-8")
        )
        dump = json.dumps(from_preview(text), ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(dump + "\n", encoding="utf-8")
        else:
            print(dump)
        return
    if args.patch:
        if not args.ops:
            raise SystemExit("--patch 需配合 --ops 传操作数组（JSON）")
        try:
            ops = json.loads(args.ops)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--ops 不是合法 JSON：{exc}")
        if not isinstance(ops, list):
            raise SystemExit("--ops 必须是 JSON 数组")
        path = Path(args.patch)
        outline = patch(json.loads(path.read_text(encoding="utf-8")), ops)
        text = json.dumps(outline, ensure_ascii=False, indent=2)
        path.write_text(text + "\n", encoding="utf-8")
        print(text)
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
