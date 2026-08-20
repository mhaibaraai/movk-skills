import { z } from 'zod'

export default defineMcpTool({
  description: '列出本服务托管的全部 Agent Skills，含名称、用途描述与可读取的文件清单。开始任何任务前先调用它挑选合适的技能。',
  annotations: {
    readOnlyHint: true,
    destructiveHint: false,
    idempotentHint: true,
    openWorldHint: false
  },
  inputSchema: {
    search: z.string().optional().describe('按名称或描述过滤技能，省略则返回全部')
  },
  inputExamples: [
    {},
    { search: '政策' },
    { search: 'web' }
  ],
  handler({ search }) {
    const skills = getSkillCatalog(useEvent())
    if (!search) {
      return { skills, total: skills.length }
    }

    const keyword = search.toLowerCase()
    const matched = skills.filter(skill =>
      skill.name.toLowerCase().includes(keyword)
      || skill.description.toLowerCase().includes(keyword)
    )

    return { skills: matched, total: matched.length }
  }
})
