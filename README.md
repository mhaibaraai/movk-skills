# movk-skills

Agent Skills 库与业务 MCP 服务。同一份技能通过两条通道对外：MCP 端点供智能体按需调用，`/.well-known/skills/` 供 Agent Skills 客户端直接安装。业务 MCP 按子前缀隔离在各自的端点上。

## 端点

| 路径 | 说明 |
| --- | --- |
| `/mcp` | 技能库 MCP 端点，公开。工具 `list-skills` / `get-skill` / `read-skill-file`，提示词 `use-skill`，每个技能的 SKILL.md 另注册为一条资源 |
| `/mcp/<业务名>` | 业务 MCP 端点，各自鉴权。当前只有示例端点 `/mcp/demo` |
| `/.well-known/skills/index.json` | Agent Skills 发现清单 |
| `/.well-known/skills/<技能名>/<文件路径>` | 技能文件原文 |

## 接入

作为 Agent Skills 安装（Claude Code、Cursor 等）：

```bash
npx skills add https://<部署域名>
```

作为 MCP Server 接入，写进 `.mcp.json`：

```json
{
  "mcpServers": {
    "movk-skills": {
      "type": "http",
      "url": "https://<部署域名>/mcp"
    }
  }
}
```

业务端点需要额外带 token：

```json
{
  "mcpServers": {
    "movk-demo": {
      "type": "http",
      "url": "https://<部署域名>/mcp/demo",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

## 技能列表

| 技能 | 说明 |
| --- | --- |
| [web-fetch](skills/web-fetch/SKILL.md) | 通用网页抓取与检索基座。两层引擎（http / browser）按需自动降级抓取 HTML/PDF 正文，sitemap 枚举与 360 搜索零 API Key 发现候选 URL。供不提供内置 WebSearch/WebFetch 的部署环境使用，也可被其他技能作为抓取底座调用。 |
| [policy-interpretation](skills/policy-interpretation/SKILL.md) | 政策法规解读助手。检索国务院政策文件库与发改委、工信部、应急管理部等八个部委的官网列表页，抓取政策原文（含 PDF 附件），从政策层级、核心条款、适用范围、时间节点、处罚条款、企业影响六个维度解读，输出深度解读 / 要点速览 / 多政策对比三类报告。零 API Key，依赖 web-fetch 基座。 |
| [petrochem-report-insights](skills/petrochem-report-insights/SKILL.md) | 石化行业研报洞察助手。检索中石化、中石油、中海油、埃克森美孚、壳牌、IEA、OPEC 等 14 家企业/机构的公开报告，从核心数据、关键结论、市场趋势、投资动态、风险、产业链影响六维度分析，输出深度分析 / 动态速览 / 多机构对比三类报告。依赖 web-fetch 基座。 |

## 开发

```bash
pnpm install       # 安装依赖（postinstall 自动 nuxt prepare）
pnpm dev           # 开发服务器 http://localhost:3000
pnpm build         # 生产构建
pnpm preview       # 预览生产构建
pnpm lint          # ESLint 检查
pnpm typecheck     # 类型检查
```

新增或修改技能、新开业务 MCP 前先读 [AGENTS.md](AGENTS.md)。
