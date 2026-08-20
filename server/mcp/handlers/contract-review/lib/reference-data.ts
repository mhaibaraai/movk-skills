import { docxToMarkdown } from './docx'
import { CONTRACT_TYPE_META, type ContractType } from './types'

export interface ContractTemplate {
  title: string
  sourceFile: string
  content: string
}

/**
 * 模板随构建产物固定不变，解析结果按 lambda 实例缓存，
 * 免得每次调用都重新解压一遍 docx。这是有意保留的可变状态。
 */
const parsed = new Map<ContractType, string>()

/**
 * data/ 下的模板走 Nitro serverAssets 读取，不用 fs。
 * 运行时拼出来的动态路径不会被打包器的依赖追踪发现，本地正常、线上 ENOENT。
 */
async function readTemplateBytes(contractType: ContractType): Promise<Uint8Array | null> {
  const raw = await useStorage('assets:contract-review')
    .getItemRaw<Uint8Array>(`templates/${contractType}.docx`)

  // dev 走 unstorage fs driver 拿到 Buffer，生产走 base64 内联拿到 Uint8Array
  return raw ? new Uint8Array(raw) : null
}

export async function getTemplate(contractType: ContractType): Promise<ContractTemplate> {
  const { title, sourceFile } = CONTRACT_TYPE_META[contractType]

  const cached = parsed.get(contractType)
  if (cached !== undefined) return { title, sourceFile, content: cached }

  const bytes = await readTemplateBytes(contractType)
  if (!bytes) {
    throw createError({
      statusCode: 404,
      message: `未收录 "${contractType}" 的标准合同模板，无法进行条款比对`
    })
  }

  let content: string
  try {
    content = docxToMarkdown(bytes)
  } catch (error: unknown) {
    throw createError({
      statusCode: 500,
      message: `标准合同模板 "${sourceFile}" 解析失败：${error instanceof Error ? error.message : String(error)}`
    })
  }

  parsed.set(contractType, content)
  return { title, sourceFile, content }
}
