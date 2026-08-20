export const CONTRACT_TYPES = ['procurement', 'construction', 'lease', 'technical-service'] as const

export type ContractType = typeof CONTRACT_TYPES[number]

export interface ContractTypeMeta {
  /** 中文类型名，用于工具入参说明与报告措辞 */
  label: string
  /** 模板标题，含模板版本号 */
  title: string
  /** 模板原始文件名。换版本时连同 data/templates/<type>.docx 一起替换 */
  sourceFile: string
}

export const CONTRACT_TYPE_META: Record<ContractType, ContractTypeMeta> = {
  'procurement': {
    label: '采购',
    title: '一般货物采购合同（单笔）标准模板 V7',
    sourceFile: '一般货物采购合同+(单笔)_V7_20241213100636620.docx'
  },
  'construction': {
    label: '施工',
    title: '小型建设工程施工合同标准模板 V8',
    sourceFile: '小型建设工程施工合同_V8_20250528152849772.docx'
  },
  'lease': {
    label: '租赁',
    title: '租赁合同（通用）标准模板 V7',
    sourceFile: '租赁合同（通用）_V7_20250528153145797.docx'
  },
  'technical-service': {
    label: '技术服务',
    title: '技术服务合同标准模板 V4',
    sourceFile: '技术服务合同（信息部新）_V4_20221105172429512.docx'
  }
}
