# AGENTS.md

movk-skills 既是 Claude Code 技能仓库，也是这些技能的 MCP 服务。仓库根目录是一个最小 Nuxt 4 应用：`skills/` 存放技能源码，构建期被扫描后通过 MCP 与 Agent Skills 两条通道对外。

## 常用命令

```bash
pnpm install       # 安装依赖（postinstall 自动 nuxt prepare）
pnpm dev           # 开发服务器 http://localhost:3000
pnpm build         # 生产构建
pnpm preview       # 预览生产构建
pnpm lint          # ESLint 检查
pnpm lint:fix      # ESLint 自动修复
pnpm typecheck     # nuxt typecheck（vue-tsc）
```

## 项目结构

```text
movk-skills/
├── modules/skills.ts                构建期扫描 skills/，登记清单、serverAssets 与预渲染路由
├── shared/skills.ts                 SkillEntry 类型，app 与 server 共用
├── server/
│   ├── utils/skills.ts              清单读取、文件读取与白名单校验的唯一实现
│   ├── routes/.well-known/skills/   Agent Skills 分发端点
│   └── mcp/
│       ├── index.ts                 默认 handler，把每个技能的 SKILL.md 注册成资源
│       ├── tools/                   → /mcp
│       ├── prompts/                 → /mcp
│       └── handlers/<业务名>/        → /mcp/<业务名>
├── app/app.vue                      落地页，兼作部署自检
└── skills/                          技能源码
```

关键约定：**技能内容的读取只走 `server/utils/skills.ts`**。路由、工具、资源、提示词都调它，不要各自读盘或重新拼路径。文件是否可对外分发由构建期生成的白名单决定，`modules/skills.ts` 的 `EXCLUDED_SEGMENTS` 同时控制白名单与 server bundle 的打包范围，改一处即可。

## 技能目录约定

```text
skills/<skill-name>/
├── SKILL.md            必需，入口与 frontmatter 配置
├── scripts/            可执行脚本，确定性工作交给它们
├── references/         按需加载的长文档（模板、schema、写作规范）
├── templates/          可复用的数据模板
├── assets/             静态资源
└── tests/              开发用，不对外分发
```

`SKILL.md` 是入口，正文写给模型看，控制在 100 行内。模板、schema、格式规范这类只在某一步用得上的长内容放进 `references/`，在正文对应步骤里用相对链接指向它，让模型需要时再读——不要一直占着上下文。

目录名必须与 frontmatter 的 `name` 完全一致，且符合 Agent Skills 命名规范（仅小写字母、数字、连字符，不以连字符开头或结尾，不含连续连字符，长度 ≤64）。不合规的技能在构建期被跳过并输出警告。

**大体积二进制资源不要入库。** 技能文件会整个打进 server bundle，几十 MB 的模板文件会直接压垮 serverless 部署。需要分发大文件时走外部存储，在 SKILL.md 里给外链。

## SKILL.md frontmatter

只允许 `name` 与 `description` 两个键，对齐官方 Agent Skills 规范（superpowers、skill-creator 等官方技能都只有这两个）。不要加 `title`、`opening`、`role`、`prompt` 这类平台专有字段——公司智能体平台已不是交付目标，加了只会让同一套工作流在一个文件里写两遍。

```yaml
---
name: policy-interpretation
description: 面向发改委、工信部、应急管理部……自动检索政策原文并解读，提炼政策层级、核心条款、适用范围、时间节点与处罚责任，输出深度解读 / 要点速览 / 多政策对比三类报告。当用户提到政策解读、法规分析、合规管理、政策影响分析时触发。
---
```

`description` 一句话讲清覆盖场景、核心能力、产出物，结尾列触发关键词。它有两个消费方：宿主据此判断是否加载该技能，`list-skills` 工具据此让模型挑技能——两边都只看得到这一个字段，关键词写全，宁多勿少。

## 脚本约定

- 用 `uv run` 调用，PEP 723 内联声明依赖，不写 `requirements.txt` 之外的安装步骤。
- **路径一律相对技能根目录**：本技能脚本写 `scripts/x.py`，跨技能写 `../web-fetch/scripts/x.py`（技能之间恒为兄弟目录）。不要写死 `skills/<技能名>/` 前缀——`npx skills add` 会把技能装进 `~/.claude/skills/<技能名>/`，此时 cwd 是项目目录、根本不含 `skills/` 目录，写死的前缀全部失效。前缀由宿主解析（Claude Code 加载技能时会告知 Base directory）。
- 每个技能的「运行约定」一节要写明前缀取法与 find 兜底（`find / -name <标志脚本>.py -not -path '*__pycache__*' 2>/dev/null | head -1`，取其上两级为技能根目录），并强调不要 `cd` 进技能目录——输出文件要落在当前工作目录。
- **不要给 `uv run` 加 timeout 参数**，沙箱后端不支持 per-command timeout override，加了必定报错。
- 脚本负责确定性工作（抓取、渲染、格式转换），模型负责判断与写作。边界要清晰。
- 日志走 stderr，结果走 stdout，便于管道消费。
- 抓取外部内容时始终校验 TLS 证书，不要为了绕过证书错误而关闭校验。

## HTTP 分发

技能不再打 zip 分发，改由服务直接提供：

| 端点 | 说明 |
| --- | --- |
| `GET /.well-known/skills/index.json` | 全部技能的清单（name、description、files） |
| `GET /.well-known/skills/<技能名>/<文件路径>` | 单个技能文件的原文 |

这两条路由在构建期全部预渲染成静态文件，部署到静态托管上直接命中 CDN，不占用 serverless 函数。客户端一条 `npx skills add https://<部署域名>` 即可安装。

MCP 通道由 `/mcp` 提供 `list-skills`、`get-skill`、`read-skill-file` 三个工具与 `use-skill` 提示词，不支持 Agent Skills 规范的客户端也能借此用上技能。

## 新开一个业务 MCP

复制 `server/mcp/handlers/demo/` 改名，如 `server/mcp/handlers/hn-petro/`。路由自动变成 `/mcp/hn-petro`，目录内 `tools/` 下的工具自动归属该 handler，不会出现在 `/mcp`（由 `defaultHandlerStrategy: 'orphans'` 保证）。

工具用 `defineMcpTool` + zod `inputSchema`，只读工具标注 `readOnlyHint: true`，错误一律 `throw createError(...)`，toolkit 会转成 MCP 合规的错误结果。

### 公开端点（无需 token）

不写 `middleware`，工具也不写 `enabled` 守卫，就这么简单。`/mcp` 默认 handler 就是这个形态。

```ts
// server/mcp/handlers/<业务名>/index.ts
export default defineMcpHandler({
  description: '...',
  instructions: '...'
})
```

### 鉴权端点

中间件只往 `event.context` 写身份，工具用 `enabled` 守卫控制可见性——未授权时工具直接不出现在列表里。token 用该业务自己的 runtimeConfig 字段，同步补 `.env.example`。

```ts
// server/mcp/handlers/<业务名>/index.ts
export default defineMcpHandler({
  description: '...',
  middleware: (event) => {
    const expected = useRuntimeConfig(event).xxxToken
    const token = getHeader(event, 'authorization')?.replace(/^Bearer\s+/i, '')
    if (expected && token === expected) {
      event.context.xxxAuthed = true
    }
  }
})

// server/mcp/handlers/<业务名>/tools/foo.ts
export default defineMcpTool({
  enabled: event => event.context.xxxAuthed === true,
  // ...
})
```

**鉴权中间件不要抛 401。** 抛了会让 MCP 客户端进入 OAuth discovery，去找并不存在的授权端点。`server/mcp/handlers/demo/` 是这个模式的现成样板。

MCP 工具与提示词的 handler 拿不到 H3 event，只能用 Nitro 的 `useEvent()`，这依赖 `nuxt.config.ts` 里的 `experimental.asyncContext`，不要关掉。

## 依赖注意事项

`pnpm-workspace.yaml` 里锁了 `overrides: h3: 1.15.11`。Nitro 2.13 用的是 h3 1.x，而 `@nuxtjs/mcp-toolkit` 的 peer 范围是 `>=1.15.11`——不锁的话会被解析到别处带进来的 h3 2.0-rc，导致 `H3Event` 出现两份互不兼容的类型，typecheck 直接崩。

## 文档风格

- 代码、注释、文档一律不用 emoji。
- 中文用全角标点，中英文之间加空格，代码标识符与数字用半角。
- 代码块必须标注语言标识符。
- 同一事实只写一处。部委列表、模板清单这类数据的唯一来源是脚本或 `references/`，SKILL.md 正文引用而不复述。
