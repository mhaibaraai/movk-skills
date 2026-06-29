# 模板速查

5 套化工专属模板，色系定义见 [templates/themes.json](../templates/themes.json)。

| 模板键 | 场景 | 主色 | 默认章节 |
|--------|------|------|----------|
| `项目汇报` | 项目/专项汇报 | 中石化红 `#C00000` | 项目背景·实施进展·成效分析·下步计划 |
| `安全生产培训` | 安全培训 | 砖红 `#B23B1E` | 安全方针·风险辨识·操作规程·应急处置 |
| `政策宣贯` | 政策解读 | 暗红 `#9A2B2B` | 政策背景·核心要点·落地举措·执行要求 |
| `危化品管理` | 危化品全周期 | 中石化红 `#C00000` | 危险特性·储运规范·管控措施·应急预案 |
| `项目申报` | 申报评审 | 深红 `#7A1620` | 申报背景·技术方案·投资效益·保障措施 |

共用：微软雅黑、16:9、顶部主色条 + 页脚机构署名。各模板差异：主色/点缀色/默认章节/页脚文案。

## 各场景建议页型组合

- 汇报：cover→toc→section→points/table→closing
- 培训：cover→toc→section→points→warning→closing（含安全提示）
- 危化品：必含 ≥1 `warning` 与 ≥1 `table`（物料台账）
- 申报：突出 `table`（投资/效益）+ section 四段式

## 资产目录

- `assets/sinopec-base.pptx` — 中石化原版模板，作 `--base` 套版示例与品牌色来源。
- `assets/logos/` — 放企业 logo（png/jpg），用 `--logo` 或大纲 `logo` 引用。

```bash
python scripts/build_pptx.py --outline outline.json \
  --base assets/sinopec-base.pptx --logo assets/logos/sinopec.png
```

