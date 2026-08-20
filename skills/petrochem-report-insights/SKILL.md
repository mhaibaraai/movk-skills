---
name: petrochem-report-insights
description: 检索中国石化、中国石油、中国海油等国内石化企业，埃克森美孚、壳牌、BP 等国际油气巨头，以及 IEA、OPEC、伍德麦肯兹、中国石油和化学工业联合会等研究机构的公开报告（年报、可持续发展报告、月度报告、研究报告），按核心数据、关键结论、市场趋势、投资动态、风险、产业链影响六个维度提炼洞察，输出深度分析 / 行业动态速览 / 多机构观点对比三类报告。当用户提到石化报告、能源研报、油气行业研究、石化市场洞察、炼化行业分析、油气企业年报解读、行业动态摘要时触发。
---

# 石化行业研报洞察

检索石油化工企业与行业研究机构的公开报告，提炼核心数据、关键观点与产业链影响，生成洞察摘要。发现与抓取能力全部来自 [web-fetch](../web-fetch/SKILL.md) 基座技能，本技能只负责机构元数据、分析维度与报告格式。

运行约定：

- 路径一律相对本技能根目录：本技能脚本写 `scripts/x.py`，web-fetch 基座写 `../web-fetch/scripts/x.py`（两者恒为兄弟目录）。执行时给命令加上技能根目录前缀，不要 `cd` 进技能目录——输出文件要落在当前工作目录。前缀取宿主加载技能时告知的 Base directory；拿不到就用 `find / -name sources.py -not -path '*__pycache__*' 2>/dev/null | head -1` 定位，取其上两级为技能根目录。
- 所有 `uv run` 命令都不要加 timeout 参数，沙箱后端不支持 per-command timeout override，加了必定报错。

## 覆盖机构

14 家机构的完整元数据（中英文名、`site_domain`、语言、典型报告类型）唯一来源是 [scripts/sources.py](scripts/sources.py)，按 `org_type` 分三类：

- `soe_domestic` 国内石化企业：`sinopec` 中国石化、`petrochina` 中国石油、`cnooc` 中国海油、`sinochem` 中化集团、`yanchang` 延长石油
- `intl_major` 国际油气巨头：`exxonmobil` 埃克森美孚、`shell` 壳牌、`bp` 英国石油、`totalenergies` 道达尔能源、`chevron` 雪佛龙
- `research_institute` 行业研究机构：`cpcif` 中国石油和化学工业联合会、`iea` 国际能源署、`opec` OPEC、`woodmac` 伍德麦肯兹

```bash
uv run scripts/sources.py --list          # 打印全部机构
uv run scripts/sources.py --show iea      # 打印单条机构详情
```

发现候选统一走 `web-fetch` 的两个通道（都用机构的 `site_domain`），不针对单个机构维护官网抓取正则——各家官网改版频繁，维护一堆正则性价比太低。sitemap 是标准协议、靠 robots.txt 自动发现，一次实现全域通用，不违反这条决策。

抓取侧：国际油气巨头官网与 IEA 多为服务端渲染，`http` 层直接拿到（约 0.3s）；国内企业官网普遍有反爬——中国石油官网是瑞数 JS 挑战（HTTP 412，TLS 指纹伪装过不去）、中国海油是 JS 空壳，这类站点由 `web-fetch` 的 `browser` 层渲染拿到正文，实测可稳定取回（瑞数需 30–45 秒等页面自行重载，属正常耗时）。

## 工作流程

### Step 1：解析需求

识别目标机构（可按 `org_type` 或常见别名匹配，如"三桶油"= sinopec+petrochina+cnooc）、主题领域（转检索关键词）、时间范围、输出格式偏好。

### Step 2：发现候选

两个通道，`--site` 均传对应机构的 `site_domain`。**默认先 sitemap，不可用再回落 search。**

```bash
uv run ../web-fetch/scripts/sitemap.py --site iea.org --match /reports/ --since 2025-01-01
uv run ../web-fetch/scripts/search.py --query "energy transition" --site shell.com --max-results 5
```

`sitemap.py` 直连原站枚举站点条目，结果带 `lastmod` 且最新在前。**"最新一期""某年以来的"这类需求必走这条；海外机构（IEA/Shell 等）也必须走这条**——实测 360 对 `site:iea.org` 只返回 1 条首页，而 IEA 自己的 sitemap 里有 2926 条报告。

`search.py` 用于模糊关键词匹配，或站点没有 sitemap 时（如 `opec.org`）。国内机构（`cnpc.com.cn`、`sinopec.com`）是它的强项——实测 `site:cnpc.com.cn` 能直接命中年度社会责任报告，`news.` 子域也在覆盖内。

`errors[].kind`：`no_sitemap` 回落 search；`no_match` 才是真无结果（可换关键词重试）；`blocked`/`network_unreachable` 说明该机构当前部署环境下抓不到（先跑 `uv run ../web-fetch/scripts/fetch.py --check-env` 确认引擎是否齐全；360 是 IP 层拦截，换个出口 IP 结论就变），如实告知用户，不要编造内容替代。

### Step 3：抓取正文

```bash
uv run ../web-fetch/scripts/fetch.py --urls '["https://...", "https://..."]' --max-chars 8000
```

`type` 区分 `html`/`pdf`。`low_confidence=true` 的 PDF 结果（疑似加密或扫描件）不可靠，如实告知用户而非当正文分析。

### Step 4：六维度分析与输出

核心数据摘录、关键结论与观点（区分机构预测 vs 已发生事实）、市场趋势与技术方向、投资与项目动态、风险与不确定性、产业链影响（上游/中游/下游分层）。

读取 [references/report-formats.md](references/report-formats.md)，按场景选格式 A（单机构深度分析）、格式 B（行业动态速览）或格式 C（多机构观点对比）。

## 质量要求

- 准确性：数字必须可追溯到原文章节/页码
- 专业性：严格区分机构预测与已发生事实
- 客观性：多机构对比不替机构下结论，呈现分歧而非强行调和
- 实用性：落到可用于决策参考的具体数据与判断
- 时效性：标注报告发布日期与覆盖周期

## 特殊处理

- 数据口径不一致（如 IEA 用 mb/d、企业年报用吨）：并列展示并注明单位，不强行换算统一
- 预测数据：必须注明发布时点（"截至 2026 年 X 月的预测"），不与已发生事实混淆
- PDF 抽取低置信度：如实告知，建议用户直接查阅原文链接
- 检索/抓取受阻：区分是"该机构无匹配结果"（`no_match`，罕见）还是"当前环境两层引擎都过不了该站点"（`blocked`），后者需如实告知环境限制，不得当作"没有这份报告"；失败结果里的 `attempts` 已逐层写明原因，直接引用
- 海外机构用 `search.py` 搜不到东西：这是 360 的索引覆盖问题，不是"该机构没发报告"——改走 `sitemap.py`
- 抓取耗时较长（国内企业官网 30–45 秒）：这是瑞数挑战需要浏览器等页面自行重载，属正常现象，不要中途改用其他来源替代

## 使用示例

典型需求到命令链路的映射见 [references/examples.md](references/examples.md)。

---

免责声明：本技能生成内容仅为公开报告的信息整理与观点摘录，不构成投资建议，具体投资决策请以机构官方发布原文及专业投资顾问意见为准。
