# 政策解读使用示例

典型需求到命令链路的映射，写新流程或拿不准参数组合时查。

> 最近发改委关于节能减排有什么新政策？帮我解读一下

部委 `ndrc`，领域节能减排 → `search.py --dept ndrc --keywords "节能减排"` → 基座 `fetch.py` 抓通知正文页 → 从结果 `attachments` 取附件 `.pdf` → `fetch.py` 抓附件全文 → 格式 A 输出。

> 工信部和应急管理部关于安全生产的最新规定有哪些？对化工企业有什么影响？

部委 `miit,mem`，领域安全生产，行业化工 → `search.py --dept miit,mem --keywords "安全生产 化工"` → 基座 `fetch.py` 抓正文 → 格式 A 输出，重点分析对化工企业的影响和合规措施。
