import type { H3Event } from 'h3'
import type { SkillEntry } from '#shared/skills'

/** 扩展名到 MIME 的映射，未收录的按二进制流处理 */
const MIME_TYPES: Record<string, string> = {
  '.md': 'text/markdown; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.yaml': 'text/yaml; charset=utf-8',
  '.yml': 'text/yaml; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.py': 'text/x-python; charset=utf-8',
  '.sh': 'text/x-shellscript; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.ts': 'text/plain; charset=utf-8'
}

export function skillFileMimeType(path: string): string {
  return MIME_TYPES[path.slice(path.lastIndexOf('.'))] || 'application/octet-stream'
}

/**
 * 构建期扫描出的技能清单，见 modules/skills.ts。
 * runtimeConfig 的生成类型只能表达结构、丢失具名类型，在这个唯一读取点收敛回 SkillEntry。
 */
export function getSkillCatalog(event: H3Event): SkillEntry[] {
  return useRuntimeConfig(event).skills.catalog as SkillEntry[]
}

export function getSkill(event: H3Event, name: string): SkillEntry {
  const skill = getSkillCatalog(event).find(s => s.name === name)
  if (!skill) {
    const available = getSkillCatalog(event).map(s => s.name).join(', ')
    throw createError({ statusCode: 404, message: `Unknown skill "${name}". Available: ${available}` })
  }
  return skill
}

/**
 * 读取技能目录内的单个文件。路径必须命中该技能 files 白名单，
 * 白名单由构建期扫描生成，因此天然排除了越界路径与不分发的目录。
 */
export async function readSkillFile(event: H3Event, name: string, path: string): Promise<string> {
  const skill = getSkill(event, name)
  if (!skill.files.includes(path)) {
    throw createError({ statusCode: 404, message: `File "${path}" is not part of skill "${name}"` })
  }

  const content = await useStorage('assets:skills').getItemRaw<string>(`${name}/${path}`)
  if (content === null || content === undefined) {
    throw createError({ statusCode: 404, message: `File "${path}" not found in skill "${name}"` })
  }

  return content.toString()
}
