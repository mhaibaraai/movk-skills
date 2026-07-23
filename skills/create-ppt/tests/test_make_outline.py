#!/usr/bin/env python3
"""make_outline 卡片序列化 round-trip 测试。纯标准库，直接运行：

    cd skills/create-ppt && uv run tests/test_make_outline.py

卡片是大纲的唯一对外形态（对话零 JSON），--from-preview 必须能从卡片
无损重建 outline——5 套模板逐一验证，另验噪声容忍与不可还原时的硬报错。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import make_outline as mo  # noqa: E402
from pptx_util import load_index, load_registry  # noqa: E402

NOISE_HEAD = "好的，已按您的要求更新大纲。\n\n"
NOISE_TAIL = "\n以上为大纲预览。要修改请直接指出；要生成请回复「生成吧」。\n[[READY]]\n"


def build_filled(key: str, pages: int = 14) -> dict:
    """骨架 + 程序化填充：每个文本槽写入不超 cap 的文案，模拟门② 填充后的状态。"""
    item = load_registry()[key]
    outline = {
        "template": key,
        "title": "测试主题",
        "pages": mo.allocate(load_index(key), pages, "测试主题", item["sections"]),
    }
    for n, page in enumerate(outline["pages"], 1):
        caps = page.get("caps", {})
        if page["kind"] == "cover":
            page["subtitle"] = "测试副标题"[: caps.get("subtitle", 10)]
        if page["kind"] == "closing":
            page["title"] = "感谢聆听"[: caps.get("title", 8)]
        for i, it in enumerate(page.get("items", [])):
            if "head" in it and page["kind"] != "toc":
                it["head"] = f"要点{n}-{i}"[: caps.get("item_head", 6)]
            if "body" in it:
                it["body"] = f"第{n}页第{i}条说明文案"[: caps.get("item_body", 40)]
    return outline


def expect_exit(text: str, hint: str) -> None:
    try:
        mo.from_preview(text)
    except SystemExit as exc:
        assert hint in str(exc), f"报错信息不含「{hint}」：{exc}"
        return
    raise AssertionError(f"未按预期报错（期望含「{hint}」）")


def main() -> None:
    for key in load_registry():
        outline = build_filled(key)
        card = NOISE_HEAD + mo.preview(outline) + NOISE_TAIL
        rebuilt = mo.from_preview(card)
        assert rebuilt == outline, f"{key}: round-trip 与原大纲不等"

        # 平台经 answer 变量传递会把全角空格归一化成半角——归一化后的卡片必须照样无损重建
        flat = mo.from_preview(card.replace("　", " "))
        assert flat == outline, f"{key}: 半角空格卡片 round-trip 与原大纲不等"
        print(f"round-trip ok: {key}（{len(outline['pages'])} 页，全角/半角分隔符均可）")

    outline = build_filled("通用模板", pages=16)
    card = mo.preview(outline)

    # 归一化带进的前导空格必须被 strip 掉，否则平白多占一个 cap 字符
    padded = card.replace("　`要点`　", " `要点`  ").replace("　模板：", "  模板： ")
    assert mo.from_preview(padded) == outline, "前导空格未被 strip"

    # 头行页数与实际页数不符 → 硬报错
    expect_exit(card.replace("共 16 页", "共 15 页", 1), "实际解析到")

    # 未知页型标签 → 硬报错
    expect_exit(card.replace("`要点`", "`图表`", 1), "未知页型标签")

    # 页型与重排结果对不上（把要点页标成章节）→ 硬报错
    expect_exit(card.replace("`要点`", "`章节`", 1), "页型对不上")

    # 卡片头行缺失 → 硬报错
    expect_exit("随便一段文本\n---\n**第 1 页**　`封面`\n# 标题", "找不到卡片头行")

    # 手写伪卡片（头行合法但正文是章节大纲，无页头无分隔线）→ 指明不是 --preview 原样输出
    fake = (
        "**示例化工 2025 年度安全生产工作汇报**　模板：年终安全工作汇报模版　共 4 页\n\n"
        "### **1. 全年安全生产总体情况**\n- 安全生产责任制落实\n- 事故与隐患数据对比\n\n"
        "### **2. 重点工作与成效**\n1. **责任体系**\n2. **隐患排查治理**\n\n"
        "### **3. 存在的主要问题**\n1. 承包商管理薄弱\n\n"
        "### **4. 2026 年工作计划**\n1. 重点装置自动化改造\n"
    )
    expect_exit(fake, "不是 --preview 的原样输出")

    # 要点条数超出模板最大容量 → 硬报错
    lines = card.splitlines()
    last_item = max(i for i, l in enumerate(lines) if l.startswith("- **"))
    for _ in range(mo.MAX_ITEMS + 1):
        lines.insert(last_item, "- **加塞** — 超出容量的条目")
    expect_exit("\n".join(lines), "没有能容纳的内容页")

    print("negative cases ok")


if __name__ == "__main__":
    main()
