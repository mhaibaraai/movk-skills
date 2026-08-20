import { readdir } from 'node:fs/promises'
import { join } from 'node:path'

/** 仅供开发、不对外分发的目录，扫描时整段跳过 */
export const EXCLUDED_SEGMENTS = new Set(['tests', '__pycache__', 'node_modules'])

/**
 * 技能目录内的相对路径是否可对外分发
 * @param {string} relPath 相对技能根目录的路径，分隔符为 /
 * @returns {boolean}
 */
export function isDistributable(relPath) {
  return !relPath.split('/').some(seg => seg.startsWith('.') || EXCLUDED_SEGMENTS.has(seg))
}

/**
 * 递归列出技能目录内可对外分发的文件
 * @param {string} dir 技能根目录的绝对路径
 * @param {string} [base] 递归用的相对路径前缀
 * @returns {Promise<string[]>} 相对技能根目录的文件路径
 */
export async function listSkillFiles(dir, base = '') {
  /** @type {string[]} */
  const files = []
  const entries = await readdir(dir, { withFileTypes: true })
  for (const entry of entries) {
    const relPath = base ? `${base}/${entry.name}` : entry.name
    if (!isDistributable(relPath)) continue
    if (entry.isDirectory()) {
      files.push(...await listSkillFiles(join(dir, entry.name), relPath))
    } else {
      files.push(relPath)
    }
  }
  return files
}
