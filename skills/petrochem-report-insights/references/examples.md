# 研报洞察使用示例

典型需求到命令链路的映射，写新流程或拿不准参数组合时查。

> IEA 和 OPEC 对全球石油需求增速的预测有什么分歧？

机构 `iea,opec` → IEA 走 `sitemap.py --site iea.org --match oil-market-report`（能直接拿到最新一期）；OPEC 无 sitemap，回落 `search.py --site opec.org` → `fetch.py` 抓正文 → 格式 C 输出。

> 帮我梳理三桶油和壳牌最新可持续发展报告里的关键数据

机构 `sinopec,petrochina,cnooc,shell` → 壳牌走 `sitemap.py --site shell.com --match sustainability`；三桶油走 `search.py`（360 对国内站覆盖好）→ `fetch.py` 抓正文 → 格式 A 或按机构拆分的多份速览。
