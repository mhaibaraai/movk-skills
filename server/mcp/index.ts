/**
 * 默认 handler（/mcp）。只覆写 resources：把每个技能的 SKILL.md 注册成一条具名资源，
 * 让支持资源选择器的客户端能直接把技能挂进上下文。
 * tools 与 prompts 留空，仍由 server/mcp/{tools,prompts}/ 自动发现。
 */
export default defineMcpHandler({
  resources: event => getSkillCatalog(event).map(skill => ({
    name: skill.name,
    title: `Skill: ${skill.name}`,
    description: skill.description,
    uri: `skill://${skill.name}/SKILL.md`,
    metadata: { mimeType: 'text/markdown' },
    handler: async (uri: URL) => ({
      contents: [{
        uri: uri.href,
        mimeType: 'text/markdown',
        text: await readSkillFile(event, skill.name, 'SKILL.md')
      }]
    })
  }))
})
