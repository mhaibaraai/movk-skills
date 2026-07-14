---
name: web-fetch
description: 网页抓取与检索基座。给 URL 返回清洗正文（HTML 提正文、PDF 抽文本），给关键词或域名返回候选 URL（sitemap 枚举 + 360 搜索，均无需 Key）。两层引擎降级：http（curl_cffi 伪装 TLS 指纹）→ browser（playwright 真实浏览器渲染），能过瑞数/加速乐/Cloudflare 这类 JS 挑战。宿主无 WebSearch/WebFetch 时可用，也是其他技能的抓取底座。当用户提到网页抓取、PDF 文本提取、站点内容枚举、sitemap、反爬绕过、瑞数/Cloudflare 验证、无 API Key 搜索、抓取引擎可用性时触发。
metadata:
  title: 网页抓取基座
  opening: |
    您好，我是网页抓取基座，可以批量抓取网页正文/PDF文本，或用 sitemap/关键词发现候选链接。
    请告诉我：① 要抓取的 URL、要枚举的站点或要检索的关键词 ② 是否需要限定域名与时间范围 ③ 对内容深度的要求（是否需要 PDF 全文）。
    - 帮我抓一下这几个网页的正文：<URL 列表>
    - 列出 iea.org 今年以来发布的所有报告
    - 用关键词「世界能源展望」在 iea.org 域名下搜一下相关报告
    - 检查一下当前环境的抓取引擎能力，哪些站点可能抓不到
  role: ""
  prompt: |
    你是网页抓取基座智能体，提供三个 CLI：scripts/fetch.py（URL -> 清洗后正文/PDF文本）、
    scripts/sitemap.py（域名 -> 候选 URL，带 lastmod）与 scripts/search.py（关键词[+域名]
    -> 候选 URL，360 搜索）。两个发现通道均零 API Key。

    【流程】
    1. 先判断任务类型：已有 URL 直接 fetch；否则先发现候选 URL 再 fetch。
    2. 发现候选，两个通道按下述优先级选：
       a) 首选 sitemap（枚举全站某类内容、取最新一期、按时间段筛）：
          uv run scripts/sitemap.py --site <域名> --match <URL 过滤正则> --since YYYY-MM-DD
          固定走 http 层，一个请求拿到，结果带 lastmod 且按其降序（最新在前）。
          kind=no_sitemap 说明该站没有 sitemap，回落到 b。
       b) 回落 search（模糊关键词匹配，或站点无 sitemap 时）：
          uv run scripts/search.py --query "<关键词>" --site <域名，可选> --max-results 10
       两者输出同构 {"results":[...],"errors":[{kind,detail}]}。
       kind=no_match 才是真的无结果（罕见），可换关键词重试；
       kind=blocked 说明两层引擎都没能过关——360 是 IP 层拦截，被拉黑的出口 IP 连真实浏览器
       也过不去，这是环境限制、不是「该查询没有结果」，须如实告知，不要编造内容替代。
       c) 两个通道都失效时（sitemap 报 no_sitemap 且 search 报 blocked）的兜底：直接抓入口页，
          从锚文本定位目标。
          uv run scripts/fetch.py --urls '["https://www.<域名>/"]' --links
          engine=auto 会自动升级到 browser 越过反爬。从 attachments[].text（附件锚文本）或
          links[].text（同域页面锚文本）里找目标，再抓那条 URL；找不到就顺着栏目页锚文本逐级跳。
          锚文本是唯一的判别依据——同页十余份 PDF 的 URL 常是拼音缩写（qyshzrbg/ndbg），
          单看 URL 分不出哪份是目标，仍然绝不允许按 URL 命名规律去猜。
    3. 抓取：uv run scripts/fetch.py --urls '["url1","url2"]' --max-chars 8000
       输出数组，每条含 engine_used/type(html|pdf)/title/length/degraded/text，
       失败则含 error/attempts。
       degraded=true 表示该 URL 必须靠浏览器渲染才拿得到，环境装不了浏览器时这类会失败。
       失败时直接引用 attempts 里的逐层 kind/detail 说明原因，不要自己猜。
       PDF 结果里 low_confidence=true 表示疑似加密或扫描件，抽取不可靠，如实告知用户而非当作正文使用。
    4. 遇到列表页/索引页状态码非 200 但仍需要具体条目时，不要固定抓该列表页，改用 sitemap.py
       或 search.py 拿更精确的条目 URL（见 references/engine-notes.md 的 IEA 案例）。

    【规范】不臆造抓取失败的原因，直接引用 error/attempts[].detail 原文；PDF 低置信度结果必须
    标注不确定性，不得当作可靠正文分析；被其他技能调用时用相对路径引用本技能脚本，不做代码级
    import（保持技能间松耦合）。所有 uv run 命令不得加 timeout 参数。
---

# 网页抓取基座

给 URL 批量返回清洗后的正文（HTML/PDF），给域名或关键词返回候选 URL。两层引擎自动降级，尽量在任意部署环境下都能工作。设计动机：技能可能部署在不提供内置 WebSearch/WebFetch 的沙箱上，抓取与发现能力必须内化进脚本本身。

运行约定：所有 `uv run` 命令都不要加 timeout 参数，沙箱后端不支持 per-command timeout override，加了必定报错。

## 两层引擎

`http`（`curl_cffi`，伪装 Chrome 的 TLS 指纹）→ `browser`（`playwright` 真实 Chromium 渲染，按需安装 headless shell）。`engine=auto` 先试 `http`，未过关才升级。

**`http` 层不只是快路径，还是所有非 HTML 资源的唯一取法**——浏览器 `goto` 一个 PDF 会直接抛 `Download is starting`（Chromium 把它当下载而非导航），XML 则会被渲染成 Chrome 的树视图 DOM 从而毁掉解析。所以 PDF、sitemap.xml、robots.txt、JSON 接口一律走 `http`；`browser` 层若需要取字节（如挑战站点后面的 PDF），改用共享 cookie 的 `context.request` 而非导航。

**`browser` 层要有耐心**：瑞数一类的 JS 挑战首跳返回 412 加混淆脚本，脚本执行后写 cookie 再自行重载，全程实测需 30–45 秒。判定「抓到了」用的是正向依据——**必须渲染出实质正文**，而不是「挑战特征消失」（挑战失败时页面会被清空，特征同样消失，那会把空壳误判成成功）。

能力边界与实测结论见 [references/engine-notes.md](references/engine-notes.md)。先跑一次 `uv run scripts/fetch.py --check-env` 了解当前部署环境的能力上限。

## 工作流程

### Step 1：发现候选（可选，已有 URL 时跳过）

两个通道，**默认先 sitemap，不可用再回落 search**。两者输出同构 `{"results":[...],"errors":[...]}`。

```bash
uv run scripts/sitemap.py --site iea.org --match /reports/ --since 2025-01-01
uv run scripts/search.py --query "world energy outlook" --site iea.org --max-results 10
```

`sitemap.py` 从 `robots.txt` 声明的 sitemap（回落 `/sitemap.xml`）枚举站点条目，`--match` 按 URL 正则过滤、`--since` 按 `<lastmod>` 筛时间，结果按 `lastmod` 降序（最新在前）。它固定走 `http` 层，一个请求拿到，且能拿到搜索引擎根本没索引的内容——**枚举某类内容、取最新一期、筛时间段一律走这条**。

`search.py` 是模糊关键词匹配的补充，`--site` 限定域名。注意 360 是 IP 层拦截，成败取决于当前环境的出口 IP。

`errors[].kind`：`no_sitemap` 说明该站没有 sitemap（回落 search）；`no_match` 才是真无结果；`blocked`/`network_unreachable` 说明两层引擎都没能过关，**这是环境限制，不等于「没有这份内容」**。选型细节见 [references/engine-notes.md](references/engine-notes.md)。

### Step 2：抓取

```bash
uv run scripts/fetch.py --urls '["https://...", "https://..."]' --max-chars 8000
```

自动识别 HTML 正文与 PDF（按 Content-Type 与 `%PDF-` 魔数判定，不看 URL 后缀——政府站常把 PDF 标成 `application/octet-stream`），分别走正文提取与 `pypdf` 文本抽取。输出每条含 `engine_used`（`http`/`browser`）、`type`（`html`/`pdf`）、`degraded`（是否必须靠浏览器渲染）。PDF 结果可能带 `low_confidence`（疑似加密/扫描件）。需要浏览器的 URL 会收拢到同一个浏览器实例下并发处理，不是每个 URL 起一个。

**HTML 结果带 `attachments`（页面确有附件时才出现）。** 每条 `{url, ext, text}`，`ext` ∈ `pdf`/`doc`/`docx`/`xls`/`xlsx`/`ofd`/`wps`，已绝对化去重，`text` 是锚文本（图片链接可能为空）。政策与报告的核心条款（指标、期限、处罚）几乎总在附件里，正文通知页往往只有一句「现将《XX》印发给你们」。**要附件全文就从 `attachments` 取 URL 再抓一次，绝不要按 URL 命名规律去猜**——猜测命中站点错误页时，返回的是一个内容完全无关的页面。同一页面里多份 PDF 单看 URL 往往分不出哪份是目标（拼音缩写路径 `qyshzrbg`/`ndbg` 之间无从选择），**`text` 是唯一的判别依据**。`.ofd` 是政务版式文件，`pypdf` 读不了，但同名 `.pdf` 通常并存，优先取 `.pdf`。

**`--links` 额外带回页内同域链接（`links` 字段）。** 每条 `{url, text}`，只收有锚文本的（导航图标一类无锚文本的链接对判别没有价值），限同域含子域名。默认关闭——几百条导航链接会淹没正文。用途是 sitemap 与 search 两个发现通道都失效时的兜底：抓入口页，顺着锚文本找到栏目页，再逐级跳到目标（见「特殊处理」）。

**正文低于 200 字符一律判失败而非返回空壳。** 挑战未通过的页面往往只剩一个标题，把它当成功返回会让调用方拿着空内容做分析——宁可报错。

失败结果带 `attempts: [{engine, kind, detail}]`，逐层说明为什么没过：`http_error`（带状态码）、`challenge`（命中挑战页）、`empty_body`（疑似 JS 空壳）、`unexpected_structure`（200 但不是预期结构）、`wrong_content_type`（请求 `.pdf` 却拿回 HTML，多为链接失效或被重定向到错误页）、`timeout`/`network`、`too_large`。报错时直接引用它，不要自己猜原因。

参数：`--max-chars`（单篇正文最大字符数，默认 8000）、`--max-pages`（PDF 最多读取页数，默认 30）、`--engine`（`auto`/`http`/`browser`）、`--no-auto-install`（禁止自动安装浏览器）。

### 原始响应体（`--raw`）

```bash
uv run scripts/fetch.py --urls '["https://.../api?q=..."]' --raw
```

跳过正文清洗与截断，返回解码后的原始响应体（`raw` 字段，附 `status`/`content_type`），供调用方自行 `json.loads` 或正则提取链接——清洗会抹掉 `href`，截断会破坏 JSON。适用于 JSON 接口与需要提链接的列表页，不支持 PDF（二进制）。

## 质量要求

- 如实反馈：抓取失败直接引用 `error` 与 `attempts` 原文，不猜测或美化失败原因
- 不臆造内容：PDF `low_confidence` 结果必须标注不确定性，不当作可靠正文使用
- 环境自知：不假设两层引擎都可用，先 `--check-env` 再决定检索策略

## 特殊处理

- 列表页/索引页状态码非常规（如 404）但内容仍可能有用：不要固定抓该页面本身，改用 `sitemap.py` 或 `search.py` 拿更精确的条目 URL（`iea.org/reports` 是典型案例，见 references）
- 关键词检索报 `blocked` 而 `--check-env` 显示引擎齐全：多半是 360 对当前出口 IP 的拦截。此时改走 `sitemap.py`（走 http 层，不依赖搜索引擎）
- 两个发现通道都失效（`sitemap.py` 报 `no_sitemap` 且 `search.py` 报 `blocked`）：不要就此断言「找不到」。改抓入口页 `uv run scripts/fetch.py --urls '["https://www.<域名>/"]' --links`，从 `attachments[].text` 或 `links[].text` 的锚文本定位目标（`engine=auto` 会自动升级到 browser 越过反爬）。CNPC 实测：首页附件锚文本直接标出「集团公司2025年社会责任报告」，`links` 里也有通往对应栏目页的入口，两条路都走得通
- PDF 加密且无法解密、或抽出文本长度接近 0（疑似扫描件）：如实告知用户，不强行分析空文本
- 单个响应体超过 30MB：直接跳过下载并报错，不做全量拉取
- 两层引擎全部命中拦截：如实告知用户当前部署环境的能力边界，不要无限重试
- 进度日志走 stderr、JSON 结果走 stdout：用管道把输出喂给 `python3 -c "json.load(...)"` 解析时**不要**加 `2>&1`，否则日志会混进 stdout 导致 JSON 解析崩溃

## 被其他技能调用

其他技能通过相对路径调用本技能的 CLI（不做代码级 import，保持松耦合）：

```bash
uv run ../web-fetch/scripts/sitemap.py --site "..." --match "..." --since YYYY-MM-DD
uv run ../web-fetch/scripts/search.py --query "..." --site "..."
uv run ../web-fetch/scripts/fetch.py --urls '[...]'
uv run ../web-fetch/scripts/fetch.py --urls '[...]' --raw   # 调用方自己解析 JSON 接口/列表页
```

部署时需与调用方技能同级放在 `skills/` 目录下。

---

本技能仅负责确定性的抓取与检索，内容的解读、总结与专业判断由调用方技能或模型完成。
