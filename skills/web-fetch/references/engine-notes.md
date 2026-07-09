# 三层引擎能力边界与降级规则

## 三层引擎

| 引擎 | 依赖 | 解决什么 | 解决不了什么 |
|------|------|----------|--------------|
| `urllib` | 无（标准库） | 服务端渲染的普通站点 | TLS 指纹拦截、JS 空壳、Cloudflare 等验证挑战 |
| `curl_cffi` | 可选，`pip install curl_cffi` | 伪装 Chrome 的 TLS/JA3 指纹，解决"握手层"拦截（`urllib` 表现为 `URLError`/TLS 握手失败） | 反爬校验码、Cloudflare 挑战页、纯前端渲染页面 |
| `playwright` | 可选，`pip install playwright` + `playwright install chromium`（或系统已装 Chrome，自动复用） | 真实浏览器渲染 JS、过 Cloudflare 验证挑战 | 需要登录态/付费墙的内容 |

`engine=auto` 时按 `urllib → curl_cffi → playwright` 顺序尝试，命中以下任一情况判定"未过关"从而升级到下一层：HTTP 状态码 ≥ 400、响应体小于 500 字节（JS 空壳特征）、标题命中验证挑战页特征（`Just a moment`/`请稍候`/`安全验证`等）。`curl_cffi`/`playwright` 未安装时自动跳过对应层，仅 `urllib` 恒定可用。

## 实测结论（决定业务侧该怎么用）

以下结论来自 `curl`/`curl_cffi` 的真实网络实测，不是理论推断：

- **国际油气巨头官网**（exxonmobil / shell / bp / totalenergies / chevron）多为服务端渲染，`urllib` 层即可命中。
- **部分国内站点是 TLS 指纹层拦截**（`curl` 表现为 exit 35 握手失败），`curl_cffi` 层可解，无需浏览器。例如中石化上市公司站 `http://www.sinopec.com/listco/`（注意用 `http://` 而非 `https://`，https 证书链握手失败）。
- **反爬校验/Cloudflare 类拦截需要 `playwright`**：中国石油官网（412）、中国海油官网（JS 空壳）、OPEC 官网（Cloudflare）均属此类，`curl_cffi` 解决不了。
- **状态码非 200 不等于"抓不到有用内容"**：例如 IEA 的 `iea.org/reports` 列表页返回 404 但响应体里仍含真实报告链接——这类"入口 URL 不是最终形态"的情况，本模块会按 `_looks_blocked` 规则视为异常并尝试升级引擎，但升级也解决不了非拦截性质的 404。**遇到这种情况不要固定抓取该列表页作为入口，改用 `search.py` 检索具体报告的真实 URL**（大概率是 200）。

## `--check-env`

```bash
uv run scripts/fetch.py --check-env
```

输出各层可用性 JSON（`urllib`/`curl_cffi`/`playwright`/`chromium`），并在 stderr 给出缺失能力的安装命令。`chromium` 探测是真实启动一次浏览器再关闭（不是仅检查文件是否存在），结果按进程缓存一次。业务技能在解读 `fetch.py`/`search.py` 输出里的 `degraded: true` 或 `engines_available` 字段时，应先跑一次 `--check-env` 确认当前部署环境的能力上限，而不是假设三层都可用。

## 依赖安装

```bash
pip install curl_cffi
pip install playwright && playwright install chromium   # 或确保系统已安装 Chrome，脚本会优先复用
```

两者均为可选依赖，不装不影响 `urllib` 层正常工作，只是覆盖面变窄。`pypdf` 是唯一的强制依赖，通过 PEP 723 内联声明，`uv run` 首次执行会自动安装（体积小，秒级）。
