import { z } from 'zod'

export default defineMcpTool({
  description: '读取指定技能的 SKILL.md 全文，即该技能的完整工作流说明。拿到后按其中的步骤执行。',
  annotations: {
    readOnlyHint: true,
    destructiveHint: false,
    idempotentHint: true,
    openWorldHint: false
  },
  inputSchema: {
    name: z.string().describe('技能名，取值见 list-skills')
  },
  inputExamples: [
    { name: 'web-fetch' },
    { name: 'policy-interpretation' }
  ],
  async handler({ name }) {
    const event = useEvent()
    const skill = getSkill(event, name)

    return {
      name: skill.name,
      description: skill.description,
      files: skill.files,
      content: await readSkillFile(event, name, 'SKILL.md')
    }
  }
})
