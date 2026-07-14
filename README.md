# movk-skills

Claude Code 技能仓库。每个技能是 `skills/<name>/` 下的一个自包含目录，也可打包后投放到其他智能体平台。

## 技能列表

| 技能 | 说明 |
| --- | --- |
| [policy-interpretation](skills/policy-interpretation/SKILL.md) | 政策法规解读助手。检索国务院政策文件库与发改委、工信部、应急管理部等八个部委的官网列表页，抓取政策原文（含 PDF 附件），从政策层级、核心条款、适用范围、时间节点、处罚条款、企业影响六个维度解读，输出深度解读 / 要点速览 / 多政策对比三类报告。零 API Key，依赖 web-fetch 基座。 |
| [create-chem-ppt](skills/create-chem-ppt/SKILL.md) | 化工 PPT 小助手。按主题、场景、总页数、核心要点生成符合中石化规范的 PPT 大纲与分页内容，内置 5 套化工模板，支持套用现有模板与企业 logo，导出可二次编辑的 `.pptx`。 |
| [web-fetch](skills/web-fetch/SKILL.md) | 通用网页抓取与检索基座。两层引擎（http / browser）按需自动降级抓取 HTML/PDF 正文，sitemap 枚举与 360 搜索零 API Key 发现候选 URL。供不提供内置 WebSearch/WebFetch 的部署环境使用，也可被其他技能作为抓取底座调用。 |
| [petrochem-report-insights](skills/petrochem-report-insights/SKILL.md) | 石化行业研报洞察助手。检索中石化、中石油、中海油、埃克森美孚、壳牌、IEA、OPEC 等 14 家企业/机构的公开报告，从核心数据、关键结论、市场趋势、投资动态、风险、产业链影响六维度分析，输出深度分析 / 动态速览 / 多机构对比三类报告。依赖 web-fetch 基座。 |

## 打包分发

```bash
scripts/pack-skill.sh policy-interpretation
```

产出 `dist/policy-interpretation.zip`，zip 内顶层目录即技能名，解压后可直接放进目标平台的 `skills/`。

## 开发

新增或修改技能前请先读 [AGENTS.md](AGENTS.md)，其中约定了目录结构、`SKILL.md` frontmatter 字段、脚本规范与文档风格。
