# 四层引擎能力边界与降级规则

## 四层引擎

| 引擎 | 依赖 | 解决什么 | 解决不了什么 |
|------|------|----------|--------------|
| `urllib` | 无（标准库） | 服务端渲染的普通站点 | TLS 指纹拦截、JS 空壳、验证挑战页 |
| `curl_cffi` | PEP 723 硬依赖，`uv run` 自动装 | 伪装 Chrome 的 TLS/JA3 指纹，解决"握手层"拦截（`urllib` 表现为 `URLError`/TLS 握手失败） | JS 挑战（瑞数/加速乐）、Cloudflare 挑战页、纯前端渲染页面 |
| `reader_proxy` | 无（标准库发一个 GET） | 远端渲染代理（默认 `r.jina.ai`）在它那边跑无头浏览器执行 JS，把结果吐回来。本地装不了浏览器的沙箱靠这层过 JS 挑战 | 需要登录态/付费墙的内容；内网地址（本层主动拒绝） |
| `playwright` | 按需自动安装（约 150MB） | 本地真实浏览器渲染，链尾兜底。代理不可用/不可信时的最后手段 | 需要登录态/付费墙的内容 |

`engine=auto` 时按 `urllib → curl_cffi → reader_proxy → playwright` 顺序尝试，命中以下任一情况判定"未过关"从而升级到下一层：HTTP 状态码 ≥ 400、响应体小于 500 字节（JS 空壳特征）、命中挑战页特征（`Just a moment`/`请稍候`/`安全验证`/`$_ts`/`__jsluid` 等）。

## 实测结论（决定业务侧该怎么用）

以下结论来自真实网络实测，不是理论推断：

- **国际油气巨头官网**（exxonmobil / shell / bp / totalenergies / chevron）多为服务端渲染，`urllib` 层即可命中，不会白白升级到代理层。
- **部分国内站点是 TLS 指纹层拦截**（`curl` 表现为 exit 35 握手失败），`curl_cffi` 层可解，无需浏览器。例如中石化上市公司站 `http://www.sinopec.com/listco/`（注意用 `http://` 而非 `https://`，https 证书链握手失败）。
- **JS 挑战站点 `curl_cffi` 一定解决不了**：中国石油官网返回 HTTP 412，响应体是瑞数的 `$_ts` 混淆脚本加 `__jsluid_s` cookie。实测补齐 Sec-Fetch 等请求头无效，`curl_cffi` 带 `impersonate=chrome124` 打过去**依然 412** —— 这类防护要求真正执行 JS，TLS 指纹伪装天然过不去。只有 `reader_proxy` 或 `playwright` 能拿到正文（实测 `reader_proxy` 可稳定取回该页正文与标题）。中国海油官网（JS 空壳）、OPEC 官网（Cloudflare）同属此类。
- **`reader_proxy` 也能救 TLS/网络层失败**：`news.cnpc.com.cn` 在本地表现为 SSL 握手超时，经代理照样取回渲染后 HTML。
- **状态码非 200 不等于"抓不到有用内容"**：例如 IEA 的 `iea.org/reports` 列表页返回 404 但响应体里仍含真实报告链接——这类"入口 URL 不是最终形态"的情况，本模块会按 `_looks_blocked` 规则视为异常并尝试升级引擎，但升级也解决不了非拦截性质的 404。**遇到这种情况不要固定抓取该列表页作为入口，改用 `search.py` 检索具体报告的真实 URL**（大概率是 200）。

## `reader_proxy` 的代价与边界

这一层把目标 URL 发给第三方服务（默认 `r.jina.ai`）代为渲染，用之前要清楚三件事：

- **URL 会外发**。内网地址（`localhost`/私有网段/`.local`）由 `_is_private_target` 主动拦截，绝不外发；但公网 URL 本身会暴露给代理方。不希望外发时用 `--no-reader-proxy` 或 `WEB_FETCH_NO_READER_PROXY=1` 关掉这一层。
- **正文不是原站直出**，而是代理渲染并转换过的结果。`engine_used` 会标成 `reader_proxy`，业务技能引用这类内容时应注明来源差异。
- **无 Key 有速率限制**。配置 `JINA_API_KEY` 环境变量可提升配额，不配也能用。`WEB_FETCH_READER_ENDPOINT` 可整体换成自建或其他供应商的端点。

## `playwright` 的按需安装

前三层都不过关时才触发，一个进程只尝试一次：先 `uv pip install --python <当前解释器> playwright`（`uv run` 建的临时环境默认不带 pip，所以不走 `python -m pip`；失败会退回 pip），再 `playwright install chromium`。下载约 150MB，进度打在 stderr，装不上会如实报错并给出手动命令，不静默吞掉。`--no-auto-install` 或 `WEB_FETCH_NO_AUTO_INSTALL=1` 可禁止（CI、离线、按流量计费的环境）。

`chromium` 探测是真实启动一次浏览器再关闭（不是仅检查文件是否存在），且优先复用系统已装的 Chrome（`channel="chrome"`），能省掉这次下载。

## `--check-env`

```bash
uv run scripts/fetch.py --check-env
```

输出各层可用性 JSON（`urllib`/`curl_cffi`/`reader_proxy`/`playwright`/`chromium`）以及代理端点与是否配置了 Key，结果按进程缓存一次。业务技能在解读 `fetch.py`/`search.py` 输出里的 `degraded: true` 或 `engines_available` 字段时，应先跑一次 `--check-env` 确认当前部署环境的能力上限，而不是假设各层都可用。

## 依赖

`pypdf` 与 `curl_cffi` 通过 PEP 723 内联声明，`uv run` 首次执行自动安装（体积小，秒级）。`reader_proxy` 只用标准库。`playwright` 不进依赖块（包 40MB + 浏览器 150MB），改为链尾兜底时按需安装，避免拖慢每一次普通抓取。
