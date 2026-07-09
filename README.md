# movk-skills

Claude Code 技能仓库。每个技能是 `skills/<name>/` 下的一个自包含目录，也可打包后投放到其他智能体平台。

## 技能列表

| 技能 | 说明 |
| --- | --- |
| [policy-interpretation](skills/policy-interpretation/SKILL.md) | 政策法规解读助手。直连发改委、工信部、应急管理部等部委官网检索政策原文，从政策层级、核心条款、适用范围、时间节点、处罚条款、企业影响六个维度解读，输出深度解读 / 要点速览 / 多政策对比三类报告。零 API Key。 |
| [create-chem-ppt](skills/create-chem-ppt/SKILL.md) | 化工 PPT 小助手。按主题、场景、总页数、核心要点生成符合中石化规范的 PPT 大纲与分页内容，内置 5 套化工模板，支持套用现有模板与企业 logo，导出可二次编辑的 `.pptx`。 |

## 打包分发

```bash
scripts/pack-skill.sh policy-interpretation
```

产出 `dist/policy-interpretation.zip`，zip 内顶层目录即技能名，解压后可直接放进目标平台的 `skills/`。

## 开发

新增或修改技能前请先读 [AGENTS.md](AGENTS.md)，其中约定了目录结构、`SKILL.md` frontmatter 字段、脚本规范与文档风格。
