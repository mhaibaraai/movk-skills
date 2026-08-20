import { strFromU8, unzipSync } from 'fflate'

const DOCUMENT_ENTRY = 'word/document.xml'

/**
 * 标签一律匹配成 `<w:x>` 或 `<w:x 属性>`，不用 `<w:x[^>]*>`。
 * docx 里存在 <w:text/>、<w:tcPr>、<w:tblPr> 这类同前缀标签，宽松写法会把
 * <w:text/> 当成 <w:t> 的开标签，抽出来的「正文」里混满 XML 属性串。
 */
const BLOCK_RE = /<w:tbl>[\s\S]*?<\/w:tbl>|<w:p(?:\s[^>]*)?\/>|<w:p(?:\s[^>]*)?>[\s\S]*?<\/w:p>/g
const ROW_RE = /<w:tr(?:\s[^>]*)?>[\s\S]*?<\/w:tr>/g
const CELL_RE = /<w:tc(?:\s[^>]*)?>[\s\S]*?<\/w:tc>/g
const TEXT_RE = /<w:t(?:\s[^>]*)?>([\s\S]*?)<\/w:t>/g

// 域代码（PAGE \* MERGEFORMAT 之类）与修订里被删掉的文字都不是正文
const DROP_RE = /<w:instrText(?:\s[^>]*)?>[\s\S]*?<\/w:instrText>|<w:delText(?:\s[^>]*)?>[\s\S]*?<\/w:delText>/g
const TAB_RE = /<w:tab\s*\/>/g
const BREAK_RE = /<w:br\s*\/?>/g

const ENTITIES: Record<string, string> = {
  '&lt;': '<',
  '&gt;': '>',
  '&quot;': '"',
  '&apos;': '\''
}

function decodeEntities(text: string): string {
  return text
    .replace(/&(?:lt|gt|quot|apos);/g, match => ENTITIES[match] ?? match)
    .replace(/&#(\d+);/g, (_, code: string) => String.fromCodePoint(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_, code: string) => String.fromCodePoint(Number.parseInt(code, 16)))
    // &amp; 必须最后解，否则 &amp;lt; 会被二次解码成 <
    .replace(/&amp;/g, '&')
}

function textOf(xml: string): string {
  const normalized = xml
    .replace(DROP_RE, '')
    .replace(TAB_RE, '<w:t>\t</w:t>')
    .replace(BREAK_RE, '<w:t>\n</w:t>')

  const parts = [...normalized.matchAll(TEXT_RE)].map(match => match[1] ?? '')
  return decodeEntities(parts.join(''))
}

/**
 * 表格转 GFM。首行当表头。
 * 已知边界：单元格里嵌套表格时，非贪婪匹配会在第一个 </w:tbl> 处截断——
 * 现有的合同模板没有嵌套表格，换模板时若出现需要改成配对扫描。
 */
function tableToMarkdown(xml: string): string {
  const rows = [...xml.matchAll(ROW_RE)]
    .map(row => [...row[0].matchAll(CELL_RE)]
      .map(cell => textOf(cell[0]).replace(/\s+/g, ' ').replace(/\|/g, '\\|').trim()))
    .filter(cells => cells.length > 0)

  const [head, ...body] = rows
  if (!head) return ''

  return [
    `| ${head.join(' | ')} |`,
    `| ${head.map(() => '---').join(' | ')} |`,
    ...body.map(cells => `| ${cells.join(' | ')} |`)
  ].join('\n')
}

/**
 * docx 字节 → Markdown 正文。只读 word/document.xml，页眉页脚不取。
 * 不做标题层级识别：这批模板的 pStyle 全是 Word 自动生成的无语义 ID（ad / p / 3 / 4），
 * 条款定位靠「第 X 条」这样的正文文字本身。
 */
export function docxToMarkdown(bytes: Uint8Array): string {
  const entries = unzipSync(bytes, { filter: file => file.name === DOCUMENT_ENTRY })
  const document = entries[DOCUMENT_ENTRY]
  if (!document) {
    throw new Error(`压缩包里没有 ${DOCUMENT_ENTRY}，不是有效的 Word 文档`)
  }

  const xml = strFromU8(document)
  const blocks: string[] = []

  for (const match of xml.matchAll(BLOCK_RE)) {
    const block = match[0]
    const text = block.startsWith('<w:tbl>') ? tableToMarkdown(block) : textOf(block).trim()
    if (text) blocks.push(text)
  }

  return blocks.join('\n\n')
}
