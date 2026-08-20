/**
 * Agent Skills 发现端点。`npx skills add https://<域名>` 从这里读取清单。
 * @see https://mcp-toolkit.nuxt.dev/getting-started/agent-skills
 */
export default defineEventHandler((event) => {
  setResponseHeader(event, 'content-type', 'application/json; charset=utf-8')
  setResponseHeader(event, 'cache-control', 'public, max-age=3600')

  return { skills: getSkillCatalog(event) }
})
