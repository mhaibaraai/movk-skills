---
name: web-fetch
description: 网页抓取与检索基座。给 URL 返回清洗正文（HTML 提正文、PDF 抽文本），给关键词返回候选 URL（360 搜索，无需 Key）。四层引擎降级：urllib → curl_cffi → reader_proxy（远端渲染代理）→ playwright，本地无浏览器也能过 JS 挑战。宿主无 WebSearch/WebFetch 时可用，也是其他技能的抓取底座。当用户提到网页抓取、PDF 文本提取、反爬绕过、瑞数/Cloudflare 验证、无 API Key 搜索、抓取引擎可用性时触发。
metadata:
  title: 网页抓取基座
  opening: |
    您好，我是网页抓取基座，可以批量抓取网页正文/PDF文本，或用关键词检索候选链接。
    请告诉我：① 要抓取的 URL 或要检索的关键词 ② 是否需要限定域名 ③ 对内容深度的要求（是否需要 PDF 全文）。
    - 帮我抓一下这几个网页的正文：<URL 列表>
    - 用关键词「世界能源展望」在 iea.org 域名下搜一下相关报告
    - 检查一下当前环境的抓取引擎能力，哪些站点可能抓不到
  role: ""
  prompt: |
    你是网页抓取基座智能体，提供两个 CLI：scripts/fetch.py（URL -> 清洗后正文/PDF文本）与
    scripts/search.py（关键词[+域名] -> 候选 URL，360 搜索零 API Key）。

    【流程】
    1. 先判断任务类型：已有 URL 直接 fetch；只有关键词/主题先 search 再 fetch。
    2. 检索：uv run scripts/search.py --query "<关键词>" --site <域名，可选> --max-results 10
       输出 {"results":[{title,url,rank}],"errors":[{kind,detail}]}。
       kind=no_match 换关键词重试；kind=blocked/network_unreachable 说明各层引擎都没能过关，
       先跑一次 --check-env 确认部署环境缺哪层，缺了就如实告知用户环境限制。
    3. 抓取：uv run scripts/fetch.py --urls '["url1","url2"]' --max-chars 8000
       输出数组，每条含 engine_used/type(html|pdf)/title/length/degraded/text，失败则含 error/tried。
       degraded=true 表示该 URL 需要增强引擎才抓到内容，若环境缺该层这条会直接失败。
       engine_used=reader_proxy 表示正文由远端渲染代理（默认 r.jina.ai）渲染后转交、不是原站直出，
       引用时须注明这一来源；不希望 URL 外发给第三方就加 --no-reader-proxy。
       PDF 结果里 low_confidence=true 表示疑似加密或扫描件，抽取不可靠，如实告知用户而非当作正文使用。
    4. 遇到列表页/索引页状态码非 200 但仍需要具体条目时，不要固定抓该列表页，改用 search.py
       检索更精确的条目 URL（见 references/engine-notes.md 的 IEA 案例）。

    【规范】不臆造抓取失败的原因，直接引用 error/errors[].detail 原文；PDF 低置信度结果必须
    标注不确定性，不得当作可靠正文分析；被其他技能调用时用相对路径引用本技能脚本，不做代码级
    import（保持技能间松耦合）。所有 uv run 命令不得加 timeout 参数。
---

# 网页抓取基座

给 URL 批量返回清洗后的正文（HTML/PDF），给关键词返回候选 URL。四层引擎自动降级，尽量在任意部署环境下都能工作。设计动机：技能可能部署在不提供内置 WebSearch/WebFetch、也装不了浏览器的沙箱上，抓取与检索能力必须内化进脚本本身。

运行约定：所有 `uv run` 命令都不要加 timeout 参数，沙箱后端不支持 per-command timeout override，加了必定报错。

## 四层引擎

`urllib`（标准库，零依赖）→ `curl_cffi`（伪装 Chrome TLS 指纹，解握手层拦截）→ `reader_proxy`（远端渲染代理执行 JS，本地只发一个 GET，解瑞数/加速乐/Cloudflare 这类 JS 挑战）→ `playwright`（本地真实浏览器，链尾兜底，缺 chromium 时按需下载安装）。

`engine=auto` 自动按序尝试并升级。关键取舍：**JS 挑战站点 `curl_cffi` 一定过不了**（TLS 指纹伪装解决不了"必须执行 JS"），沙箱里也装不动浏览器，所以主力手段是把渲染搬到远端的 `reader_proxy`。代价是目标 URL 会外发给第三方，且正文是代理转换后的结果而非原站直出——内网地址本层直接拒绝，不想外发就 `--no-reader-proxy`。能力边界与实测结论见 [references/engine-notes.md](references/engine-notes.md)。

先跑一次 `uv run scripts/fetch.py --check-env` 了解当前部署环境的能力上限。

## 工作流程

### Step 1：检索（可选，已有 URL 时跳过）

```bash
uv run scripts/search.py --query "world energy outlook" --site iea.org --max-results 10
```

`--site` 限定域名（对应 `site:` 语法），不传则不限。输出 `{"results":[...],"errors":[...]}`，`errors[].kind`：`no_match` 换关键词重试；`blocked`/`network_unreachable` 说明三层引擎都没能过关，先确认 `--check-env` 是否缺层。

### Step 2：抓取

```bash
uv run scripts/fetch.py --urls '["https://...", "https://..."]' --max-chars 8000
```

自动识别 HTML 正文与 PDF（按 Content-Type 而非 URL 后缀判定），分别走正文提取与 `pypdf` 文本抽取。输出每条含 `engine_used`（实际生效的引擎）、`type`（`html`/`pdf`）、`degraded`（是否需要增强引擎才抓到）。PDF 结果可能带 `low_confidence`（疑似加密/扫描件）。

参数：`--max-chars`（单篇正文最大字符数，默认 8000）、`--max-pages`（PDF 最多读取页数，默认 30）、`--engine`（`auto`/`urllib`/`curl_cffi`/`playwright`，强制指定某一层）、`--max-workers`（并发数，默认 5）。

### 原始响应体（`--raw`）

```bash
uv run scripts/fetch.py --urls '["https://.../api?q=..."]' --raw
```

跳过正文清洗与截断，返回解码后的原始响应体（`raw` 字段，附 `status`/`content_type`），供调用方自行 `json.loads` 或正则提取链接——清洗会抹掉 `href`，截断会破坏 JSON。适用于 JSON 接口与需要提链接的列表页，不支持 PDF（二进制）。

## 质量要求

- 如实反馈：抓取失败直接引用 `error` 原文，不猜测或美化失败原因
- 不臆造内容：PDF `low_confidence` 结果必须标注不确定性，不当作可靠正文使用
- 环境自知：不假设三层引擎都可用，先 `--check-env` 再决定检索策略

## 特殊处理

- 列表页/索引页状态码非常规（如 404）但内容仍可能有用：不要固定抓该页面本身，改用 `search.py` 检索更精确的条目 URL（`iea.org/reports` 是典型案例，见 references）
- PDF 加密且无法解密、或抽出文本长度接近 0（疑似扫描件）：如实告知用户，不强行分析空文本
- 单个响应体超过 30MB：直接跳过下载并报错，不做全量拉取
- 三层引擎全部不可用或全部命中拦截：如实告知用户当前部署环境的能力边界，不要无限重试

## 被其他技能调用

其他技能通过相对路径调用本技能的 CLI（不做代码级 import，保持松耦合）：

```bash
uv run ../web-fetch/scripts/search.py --query "..." --site "..."
uv run ../web-fetch/scripts/fetch.py --urls '[...]'
uv run ../web-fetch/scripts/fetch.py --urls '[...]' --raw   # 调用方自己解析 JSON 接口/列表页
```

部署时需与调用方技能同级放在 `skills/` 目录下。

---

本技能仅负责确定性的抓取与检索，内容的解读、总结与专业判断由调用方技能或模型完成。
