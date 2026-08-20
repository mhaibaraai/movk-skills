/**
 * 示例业务 handler，暴露在 /mcp/demo。
 *
 * 新开一个业务 MCP：复制本目录改名（如 handlers/hn-petro/），路由自动变成 /mcp/hn-petro，
 * 目录内 tools/ 下的工具会自动归属该 handler，不会出现在 /mcp。
 */
export default defineMcpHandler({
  description: '示例业务 handler，复制本目录改名即可新开一个业务 MCP',
  instructions: '本端点的工具需要 Bearer token。未授权时工具列表为空。',
  middleware: (event) => {
    const expected = useRuntimeConfig(event).mcpDemoToken
    const token = getHeader(event, 'authorization')?.replace(/^Bearer\s+/i, '')

    // 刻意不抛 401：抛了会让 MCP 客户端进入 OAuth discovery，去找并不存在的授权端点。
    // 正确做法是只写身份到 context，由工具的 enabled 守卫决定可见性。
    if (expected && token === expected) {
      event.context.mcpAuthed = true
    }
  }
})
