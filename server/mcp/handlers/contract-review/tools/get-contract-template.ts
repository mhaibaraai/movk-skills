import { z } from 'zod'
import { CONTRACT_TYPE_META, CONTRACT_TYPES } from '../lib/types'
import { getTemplate } from '../lib/reference-data'

const typeHint = CONTRACT_TYPES.map(type => `${type} ${CONTRACT_TYPE_META[type].label}`).join(' / ')

export default defineMcpTool({
  description: '按合同类型返回公司标准合同模板的完整正文（Markdown）。'
    + '应在读完合同正文、判断出合同类型之后调用。'
    + '返回的正文是「条款缺失」判定的唯一依据——模板里没有的条款一律不存在，不要据此推测或补全。',
  annotations: {
    readOnlyHint: true,
    destructiveHint: false,
    idempotentHint: true,
    openWorldHint: false
  },
  enabled: event => event.context.contractReviewAuthed === true,
  inputSchema: {
    contractType: z.enum(CONTRACT_TYPES)
      .describe(`合同类型：${typeHint}，需先从合同正文判断得出`)
  },
  inputExamples: [
    { contractType: 'technical-service' },
    { contractType: 'procurement' }
  ],
  async handler({ contractType }) {
    const template = await getTemplate(contractType)
    return { contractType, ...template }
  }
})
