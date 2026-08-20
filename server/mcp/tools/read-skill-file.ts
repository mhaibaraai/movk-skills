import { z } from 'zod'

export default defineMcpTool({
  description: '读取技能目录内的单个文件（references/ 长文档、scripts/ 脚本等）。按需读取，不要一次性把整个技能的文件全拉进上下文。',
  annotations: {
    readOnlyHint: true,
    destructiveHint: false,
    idempotentHint: true,
    openWorldHint: false
  },
  inputSchema: {
    name: z.string().describe('技能名，取值见 list-skills'),
    path: z.string().describe('技能目录内的相对路径，取值见 list-skills 或 get-skill 返回的 files')
  },
  inputExamples: [
    { name: 'web-fetch', path: 'scripts/fetch.py' },
    { name: 'policy-interpretation', path: 'references/report-formats.md' }
  ],
  async handler({ name, path }) {
    const event = useEvent()

    return {
      name,
      path,
      mimeType: skillFileMimeType(path),
      content: await readSkillFile(event, name, path)
    }
  }
})
