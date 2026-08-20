import { z } from 'zod'

/**
 * 把一个技能装配成可直接执行的提示词。
 * 不支持 Agent Skills 规范的 MCP 客户端也能借此一键载入技能工作流。
 */
export default defineMcpPrompt({
  description: '载入指定技能的工作流，让模型直接按该技能执行任务',
  inputSchema: {
    name: z.string().describe('技能名，取值见 list-skills 工具')
  },
  async handler({ name }) {
    const event = useEvent()
    const skill = getSkill(event, name)
    const content = await readSkillFile(event, name, 'SKILL.md')

    return [
      `请按以下 Agent Skill 的工作流执行接下来的任务。技能名：${skill.name}。`,
      `技能目录内的其余文件用 read-skill-file 工具按需读取，可用路径：${skill.files.join('、')}。`,
      '',
      content
    ].join('\n')
  }
})
