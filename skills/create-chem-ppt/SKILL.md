---
name: create-chem-ppt
description: 化工方向 PPT 生成智能体。面向化工企业专项汇报、项目汇报、安全生产培训、政策宣贯、危化品管理、项目申报等场景，按主题/场景/总页数/核心要点自动生成符合中石化规范的 PPT 大纲与分页内容，导出可二次编辑的 .pptx。触发：生成PPT、汇报材料、安全培训课件、政策宣贯、危化品PPT、项目申报。
---

# 化工 PPT 生成指南

将用户的「主题 + 场景 + 总页数 + 核心要点」转为符合中石化品牌规范、可二次编辑的 PPT。
模型负责内容（按规范展开大纲、分配章节），脚本负责排版（python-pptx 渲染品牌一致页面）。

## 5 套模板

`项目汇报` / `安全生产培训` / `政策宣贯` / `危化品管理` / `项目申报`。详见 `references/template-catalog.md`。

## 参考文档（按需加载）

| 文件 | 何时读 |
|------|--------|
| `references/template-catalog.md` | 选模板/确认色系页型 |
| `references/outline-schema.md` | 编写大纲 JSON 时 |
| `references/writing-guide.md` | 展开正文与页数分配 |

## 工作流程

### 1. 确认参数
主题、场景（→模板键）、总页数、核心要点。缺失则按场景默认章节补全。

### 2. 生成大纲骨架（自动分配章节）
```bash
python scripts/make_outline.py --template 安全生产培训 --pages 12 \
  --title "动火作业安全培训" --sections "风险辨识,作业票证,监护要求,应急处置" > outline.json
```

### 3. 填充正文
按 `references/writing-guide.md` 将骨架占位（`{要点}`）替换为真实内容；危化品含 `table`+`warning`，单页要点 ≤6。

### 4. 渲染导出
```bash
python scripts/build_pptx.py --outline outline.json --out 输出.pptx
```
套用客户现有模板与 logo：
```bash
python scripts/build_pptx.py --outline outline.json --base 客户模板.pptx --logo assets/logo.png
```

### 5. 二次调整
产物为标准 OOXML，可在 PowerPoint/WPS 直接编辑。

## 校验
```bash
python scripts/inspect_template.py 输出.pptx   # 核对页数/版式
```

## 依赖
`pip install python-pptx`
