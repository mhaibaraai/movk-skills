<script setup lang="ts">
import type { SkillEntry } from '#shared/skills'

const { data } = await useFetch<{ skills: SkillEntry[] }>('/.well-known/skills/index.json')
const skills = computed(() => data.value?.skills ?? [])

const origin = useRequestURL().origin
const mcpJson = computed(() => JSON.stringify({
  mcpServers: {
    'movk-skills': { type: 'http', url: `${origin}/mcp` }
  }
}, null, 2))

useHead({
  title: 'Movk Skills',
  meta: [{ name: 'description', content: 'Agent Skills 库与业务 MCP 服务' }]
})
</script>

<template>
  <main>
    <h1>Movk Skills</h1>
    <p class="lead">
      Agent Skills 库与业务 MCP 服务。技能通过 MCP 与 Agent Skills 两条通道对外，业务工具按子前缀隔离。
    </p>

    <h2>端点</h2>
    <table>
      <thead>
        <tr><th>路径</th><th>说明</th></tr>
      </thead>
      <tbody>
        <tr>
          <td><code>/mcp</code></td>
          <td>技能库 MCP 端点，公开。工具 list-skills / get-skill / read-skill-file，提示词 use-skill</td>
        </tr>
        <tr>
          <td><code>/mcp/demo</code></td>
          <td>示例业务端点，需 Bearer token。新业务复制 <code>server/mcp/handlers/demo/</code> 改名即可</td>
        </tr>
        <tr>
          <td><code>/.well-known/skills/index.json</code></td>
          <td>Agent Skills 发现清单</td>
        </tr>
        <tr>
          <td><code>/.well-known/skills/{skill}/{file}</code></td>
          <td>技能文件原文</td>
        </tr>
      </tbody>
    </table>

    <h2>接入</h2>
    <p>作为 Agent Skills 安装（Claude Code、Cursor 等）：</p>
    <pre><code>npx skills add {{ origin }}</code></pre>

    <p>作为 MCP Server 接入，写进 <code>.mcp.json</code>：</p>
    <pre><code>{{ mcpJson }}</code></pre>

    <h2>技能（{{ skills.length }}）</h2>
    <ul class="skills">
      <li
        v-for="skill in skills"
        :key="skill.name"
      >
        <h3>
          <a :href="`/.well-known/skills/${skill.name}/SKILL.md`"><code>{{ skill.name }}</code></a>
          <span class="count">{{ skill.files.length }} 个文件</span>
        </h3>
        <p>{{ skill.description }}</p>
      </li>
    </ul>
  </main>
</template>

<style>
:root {
  --bg: #fff;
  --fg: #1a1a1a;
  --muted: #6b7280;
  --border: #e5e7eb;
  --code-bg: #f6f7f9;
  --link: #2563eb;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117;
    --fg: #e6edf3;
    --muted: #8b949e;
    --border: #262c36;
    --code-bg: #161b22;
    --link: #6ea8fe;
  }
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  line-height: 1.7;
}

main {
  max-width: 52rem;
  margin: 0 auto;
  padding: 3rem 1.25rem 5rem;
}

h1 { font-size: 1.9rem; margin: 0 0 .5rem; }
h2 { font-size: 1.2rem; margin: 2.5rem 0 .75rem; }
h3 { font-size: 1rem; margin: 0 0 .25rem; display: flex; align-items: baseline; gap: .6rem; }
p { margin: .5rem 0; }
.lead { color: var(--muted); }

a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }

code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: .875em;
  background: var(--code-bg);
  padding: .1em .4em;
  border-radius: 4px;
}

pre {
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: .9rem 1rem;
  overflow-x: auto;
}

pre code { background: none; padding: 0; }

table {
  width: 100%;
  border-collapse: collapse;
  display: block;
  overflow-x: auto;
}

th, td {
  text-align: left;
  padding: .5rem .75rem;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}

th { color: var(--muted); font-weight: 500; font-size: .875rem; }

.skills { list-style: none; padding: 0; }
.skills li { padding: 1rem 0; border-bottom: 1px solid var(--border); }
.skills p { margin: 0; color: var(--muted); font-size: .9375rem; }
.count { color: var(--muted); font-size: .8125rem; font-weight: 400; }
</style>
