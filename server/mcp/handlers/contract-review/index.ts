/**
 * 合同辅助审查的参考数据端点，暴露在 /mcp/contract-review。
 *
 * 只提供标准合同模板：压缩包的下载、解压与文本抽取由 skills/contract-review
 * 在平台沙箱里完成——平台域名仅内网可达，本服务端根本取不到用户上传的文件。
 */
export default defineMcpHandler({
  description: '合同辅助审查的参考数据：按合同类型提供公司标准合同模板的完整正文，供条款缺失与模板差异比对',
  instructions: [
    '推荐编排顺序：',
    '1) 先用 contract-review 技能调用 file-extract 解析合同压缩包，拿到合同正文与全部签约依据材料；',
    '2) 从合同正文判断类型（procurement 采购 / construction 施工 / lease 租赁 / technical-service 技术服务），',
    '再调用 get-contract-template 取该类型的标准模板正文，逐条比对条款缺失与实质改写。',
    '模板未收录（404）时要在报告中如实说明「未取得标准模板，条款缺失项未做比对」，',
    '不要虚构模板中不存在的条款。',
    '本端点需要 Bearer token，未授权时上述工具不可见。'
  ].join(''),
  middleware: (event) => {
    const expected = useRuntimeConfig(event).mcpContractReviewToken
    const token = getHeader(event, 'authorization')?.replace(/^Bearer\s+/i, '')

    // 刻意不抛 401：抛了会让 MCP 客户端进入 OAuth discovery，去找并不存在的授权端点。
    if (expected && token === expected) {
      event.context.contractReviewAuthed = true
    }
  }
})
