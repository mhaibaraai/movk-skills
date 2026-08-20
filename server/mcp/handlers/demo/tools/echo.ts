import { z } from 'zod'

export default defineMcpTool({
  description: '原样回显输入，用于验证 /mcp/demo 的鉴权与连通性',
  annotations: {
    readOnlyHint: true,
    destructiveHint: false,
    idempotentHint: true,
    openWorldHint: false
  },
  // 未授权时工具直接不可见，而不是调用后报错
  enabled: event => event.context.mcpAuthed === true,
  inputSchema: {
    message: z.string().describe('要回显的文本')
  },
  inputExamples: [
    { message: 'hello' }
  ],
  handler({ message }) {
    return { message, at: new Date().toISOString() }
  }
})
