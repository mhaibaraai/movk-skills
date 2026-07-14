# 两层引擎能力边界与降级规则

## 两层引擎

| 引擎 | 依赖 | 解决什么 | 解决不了什么 |
|------|------|----------|--------------|
| `http` | `curl_cffi`，PEP 723 硬依赖，`uv run` 自动装 | 直发请求并伪装 Chrome 的 TLS/JA3 指纹。服务端渲染的站点一个请求拿到；**所有非 HTML 资源（PDF / sitemap.xml / robots.txt / JSON）只能走这层** | JS 挑战（瑞数/加速乐）、Cloudflare 挑战页、纯前端渲染的空壳页 |
| `browser` | 按需自动安装 playwright + headless shell | 真实 Chromium 渲染并执行 JS：JS 空壳、瑞数挑战、搜索引擎结果页 | 需要登录态/付费墙的内容；被出口 IP 拉黑的站点（换浏览器也没用） |

`engine=auto` 时先试 `http`，未过关才升级到 `browser`。判定「未过关」：HTTP ≥ 400、响应体小于 500 字节（JS 空壳特征）、命中挑战页特征（`Just a moment`/`请稍候`/`安全验证`/`访问异常`/`$_ts`/`__jsluid`）、**调用方传入的 `expect` 哨兵不匹配**。

为什么不是「只留 playwright」：浏览器是获取字节的**错误工具**。`page.goto()` 打开一个 PDF 会直接抛 `Download is starting`（Chromium 把它当下载而非导航），XML 会被渲染成 Chrome 自己的树视图 DOM 从而毁掉 `<loc>` 解析。而石化年报、可持续发展报告绝大多数是 PDF。`http` 层还顺带是快路径——海外机构官网 0.3s 直出，起浏览器要 3–16s。

为什么不是「保留 urllib」：`curl_cffi` 是它的严格超集（普通请求照发，还多了指纹伪装），且已是硬依赖。两层做同一件事，删掉冗余的那层。

### 正文判据必须是正向的

**这是本模块最容易踩的坑，踩过一次，代价是把失败伪装成了成功。**

曾经用「挑战特征消失」来反向判定通过。瑞数挑战失败时会把页面**清空**——特征随之消失，于是空壳被判为通过，返回一条只剩 `<title>` 的「成功」（`length: 20`），下游技能拿着 20 个字符去写深度分析。

现在的判据是正向的：渲染后必须出现 `MIN_TEXT_CHARS`（200）以上的实质正文才算过关，`_wait_for_body` 会一直轮询到正文出现或超时。`fetch_one` 再加一道兜底——清洗后正文短于阈值一律判失败。**宁可报错，不可返回空壳。**

顺带一提，这条修复还意外地把瑞数站点抓通了：旧逻辑在页面被清空的瞬间就返回，新逻辑继续等，瑞数脚本得以跑完并重载出真内容（见下）。

### `expect` 结构哨兵

上面那串特征是黑名单，而黑名单必然漏。拦截页完全可以做到 HTTP 200、体积正常、且不含任何已知特征——360 的「访问异常页面」就是（详见下节），它一度让整个检索层静默失效。

所以 `fetch_bytes(url, expect=<正则>)` 允许调用方反过来声明「一个**有效**响应长什么样」，不匹配即视为未过关、继续升级引擎。这是白名单，比黑名单可靠。哨兵须落在响应头部 4096 字节内（`<title>` 通常满足）。`search.py` 用它锚定 360 SERP 的标题特征，`sitemap.py` 用它锚定 `<urlset>`/`<sitemapindex>` 根标签、防止把反爬页或软 404 的 HTML 当成空 sitemap。

## 实测结论（决定业务侧该怎么用）

以下结论来自真实网络实测，不是理论推断：

- **国际油气巨头官网**（exxonmobil / shell / bp / totalenergies / chevron）与 IEA 多为服务端渲染，`http` 层即可命中（约 0.3s），不会白白起浏览器。
- **JS 挑战站点 `http` 层一定解决不了**：中国石油官网返回 HTTP 412，响应体是瑞数的 `$_ts` 混淆脚本加 `__jsluid_s` cookie。实测补齐 Sec-Fetch 等请求头无效，`curl_cffi` 带 `impersonate=chrome` 打过去**依然 412**——这类防护要求真正执行 JS，TLS 指纹伪装天然过不去。
- **瑞数站点浏览器能过，但必须有耐心**：`www.cnpc.com.cn` 首跳 412 + 混淆脚本，脚本执行后写 cookie 再**自行重载**，重载期间取内容会抛异常（不等于已通过）。全程实测约需 30–45 秒，20 秒的超时必然误杀。等到正文出来后可稳定取回全文（实测 2762 字）。`news.cnpc.com.cn` 实测 31.6 秒、中国海油（JS 空壳）2.3 秒。
- **PDF 走浏览器必须绕开导航**：`page.goto(pdf)` 抛 `Download is starting`。改用 `context.request.get()`（APIRequestContext）取字节——它共享浏览器上下文已通过挑战的 cookie，且不触发下载行为。实测可正常抽出 14 页、8 万字。
- **状态码非 200 不等于「抓不到有用内容」**：例如 IEA 的 `iea.org/reports` 列表页返回 404 但响应体里仍含真实报告链接。**遇到这种情况不要固定抓取该列表页作为入口，改用 `sitemap.py` 或 `search.py` 拿具体报告的真实 URL**（大概率是 200）。
- **360 搜索是 IP 层拦截，结论随出口 IP 变**：可疑 IP 会拿到「访问异常页面」——HTTP **200**、约 10KB、0 个结果块。补请求头无效，TLS 指纹伪装也无效。干净 IP 下 `browser` 层能拿到真结果；**被拉黑的 IP 连真实浏览器也过不去**（本机实测即如此）。所以 `search.py` 报 `blocked` 时，要如实告知用户这是当前环境出口 IP 的问题，**不是「该查询没有结果」**——不要把这条结论写死成某个固定答案。
- **搜索引擎的覆盖率是倾斜的，别把它当唯一发现通道**：360 对国内站点覆盖不错（`site:cnpc.com.cn` 能直接命中年度社会责任报告，含 `news.` 子域），但对海外机构基本没有——`site:iea.org` 只返回 1 条首页，`site:opec.org` 返回的标题是「请稍候…」（360 自己也没抓穿 OPEC 的挑战页）。而 IEA 自己的 `sitemap.xml` 直连 200、含 2926 条报告且每条带 `lastmod`。**枚举类与时间范围类需求应首选 `sitemap.py`**，见下节。

## 两个发现通道怎么选

| | `sitemap.py` | `search.py` |
|---|---|---|
| 擅长 | 枚举全站某类内容、取最新一期、按时间段筛（带 `lastmod`） | 模糊关键词匹配、跨站找线索 |
| 覆盖 | 站点自己声明的全量条目 | 取决于搜索引擎索引了什么（海外站几乎为零） |
| 链路 | 固定 `http` 层，一个请求拿到，不必起浏览器 | 常态化经 `browser` 层，且成败取决于出口 IP |
| 失效场景 | 站点没有 sitemap（如 OPEC，报 `no_sitemap`） | 出口 IP 被搜索引擎拉黑；引擎没索引该站 |

**默认先 `sitemap.py`，不可用再回落 `search.py`。** 两者输出同构（`{"results":[...],"errors":[...]}`），调用方可以统一处理。

关于「再加一个搜索引擎」：实测否决。Bing 即使经浏览器渲染仍返回 0 个结果块，DuckDuckGo html/lite 返回反爬挑战页，百度跳验证码——360 仍是唯一可脚本化的无 Key 搜索端点。检索源的多样性要靠 sitemap 这类**结构化索引源**去补，不是靠堆搜索引擎。

## 浏览器的按需安装

`http` 层不过关时才触发，一个进程只尝试一次：先 `uv pip install --python <当前解释器> playwright`（`uv run` 建的临时环境默认不带 pip，所以不走 `python -m pip`；失败会退回 pip），**装完立刻重新探测一次**——包缺失时探不到浏览器，不重探就会把已缓存的浏览器又下一遍。确实缺浏览器时才执行 `playwright install chromium --only-shell`。

`--only-shell` 不是可选优化：`playwright install chromium` 会**同时**装完整 chromium（约 344MB）和 headless shell（约 192MB），而本模块全程 `headless=True`，那 344MB 一次都用不到。

`chromium` 探测是真实启动一次浏览器再关闭（不是仅检查文件是否存在），且优先复用系统已装的 Chrome（`channel="chrome"`），命中就完全免下载。`--no-auto-install` 或 `WEB_FETCH_NO_AUTO_INSTALL=1` 可禁止安装（CI、离线、按流量计费的环境）。

**一个浏览器实例服务整批 URL**：`fetch_many` 把需要渲染的 URL 收拢到同一个 browser + context 下，多个 page 经 `asyncio.gather` 并发（`BROWSER_CONCURRENCY` 上限）。曾经是每个 URL 起一个 chromium，5 并发就是 5 个浏览器进程。实测 launch 只要 0.3s，瓶颈在页面加载而非启动。

### 关于用 MCP 或 npm 装浏览器

都试过，都否决：

- **MCP 不可行**：`engines.py` 是 `uv run` 拉起的独立 Python 子进程，MCP 工具只存在于宿主 agent 的工具层，子进程拿不到。而本技能的立身前提正是「宿主什么都不提供也能跑」，依赖 MCP 等于推翻它。`@playwright/mcp` 自己也要下同一份浏览器。
- **npm 无收益**：node 版与 Python 版 playwright 共用同一个浏览器缓存目录（`~/.cache/ms-playwright`），但下载量相同、Python 侧仍需 playwright 包来驱动，且浏览器 build revision 按 playwright 版本 pin 死，版本错配反而会各下一份。

## `--check-env`

```bash
uv run scripts/fetch.py --check-env
```

输出各层可用性 JSON（`http`/`browser`/`chromium`），结果按进程缓存一次。业务技能在解读 `fetch.py`/`search.py` 输出里的 `degraded: true` 或 `engines_available` 字段时，应先跑一次 `--check-env` 确认当前部署环境的能力上限，而不是假设两层都可用。

## 失败要说清是哪一层、为什么

失败结果带 `attempts: [{engine, kind, detail}]`，逐层记录。`kind` 取值：

| kind | 含义 |
|------|------|
| `http_error` | HTTP ≥ 400（`detail` 带状态码） |
| `challenge` | 命中反爬/JS 挑战页特征 |
| `empty_body` | 响应体过小，疑似 JS 空壳或挑战未通过 |
| `unexpected_structure` | HTTP 200 但不符合 `expect` 哨兵，疑似拦截页或站点改版 |
| `timeout` / `network` | 超时 / 其他网络层异常 |
| `too_large` | 超过 30MB 上限，已跳过下载 |

把所有失败压成一句「所有引擎均失败」会让人无从判断到底是站点拦截、网络超时还是环境缺层——`attempts` 就是为此存在的，调用方报错时应直接引用它，不要自己猜原因。

## 依赖

`pypdf` 与 `curl_cffi` 通过 PEP 723 内联声明，`uv run` 首次执行自动安装（体积小，秒级）。`playwright` 不进依赖块（包 40MB + 浏览器 192MB），改为 `http` 层不过关时按需安装，避免拖慢每一次普通抓取。
