import { existsSync } from 'node:fs'
import { readdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { defineNuxtModule, logger } from '@nuxt/kit'
import { parse as parseYaml } from 'yaml'
import type { SkillEntry } from '../shared/skills'

const SKILL_NAME_REGEX = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/
const MAX_NAME_LENGTH = 64

/** 仅供开发、不对外分发的目录，扫描时整段跳过 */
const EXCLUDED_SEGMENTS = new Set(['tests', '__pycache__', 'node_modules'])

const log = logger.withTag('skills')

/**
 * 构建期扫描根目录 skills/，把每个合规技能登记为：
 * - `runtimeConfig.skills.catalog` 的一条清单
 * - Nitro serverAssets `assets:skills` 下的文件
 * - 预渲染路由，使技能文件在静态托管上直接命中 CDN
 */
export default defineNuxtModule({
  meta: {
    name: 'skills'
  },
  async setup(_options, nuxt) {
    const skillsDir = join(nuxt.options.rootDir, 'skills')
    if (!existsSync(skillsDir)) {
      log.warn('skills/ directory not found, skipping')
      return
    }

    const catalog = await scanSkills(skillsDir)
    if (!catalog.length) {
      log.warn('no valid skill found in skills/')
      return
    }

    log.info(`Found ${catalog.length} agent skill${catalog.length > 1 ? 's' : ''}: ${catalog.map(s => s.name).join(', ')}`)

    nuxt.options.runtimeConfig.skills = { catalog }

    nuxt.hook('nitro:config', (nitroConfig) => {
      nitroConfig.serverAssets ||= []
      nitroConfig.serverAssets.push({
        baseName: 'skills',
        dir: skillsDir,
        // 与 isDistributable 共用同一份黑名单，不分发的文件也不进 server bundle
        ignore: [...EXCLUDED_SEGMENTS].flatMap(seg => [`**/${seg}`, `**/${seg}/**`])
      })

      nitroConfig.prerender ||= {}
      nitroConfig.prerender.routes ||= []
      nitroConfig.prerender.routes.push('/.well-known/skills/index.json')
      for (const skill of catalog) {
        for (const file of skill.files) {
          nitroConfig.prerender.routes.push(`/.well-known/skills/${skill.name}/${file}`)
        }
      }
    })
  }
})

function parseFrontmatter(content: string): { name?: string, description?: string } | null {
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n---/)
  if (!match?.[1]) return null
  try {
    return parseYaml(match[1])
  } catch {
    return null
  }
}

function validateSkillName(name: string, dirName: string): boolean {
  if (name.length > MAX_NAME_LENGTH) {
    log.warn(`Skill "${name}" exceeds ${MAX_NAME_LENGTH} character limit`)
    return false
  }
  if (!SKILL_NAME_REGEX.test(name) || name.includes('--')) {
    log.warn(`Skill name "${name}" does not match the Agent Skills naming spec`)
    return false
  }
  if (name !== dirName) {
    log.warn(`Skill name "${name}" does not match directory name "${dirName}"`)
    return false
  }
  return true
}

function isDistributable(relPath: string): boolean {
  return !relPath.split('/').some(seg => seg.startsWith('.') || EXCLUDED_SEGMENTS.has(seg))
}

async function listFilesRecursively(dir: string, base: string = ''): Promise<string[]> {
  const files: string[] = []
  const entries = await readdir(dir, { withFileTypes: true })
  for (const entry of entries) {
    const relPath = base ? `${base}/${entry.name}` : entry.name
    if (!isDistributable(relPath)) continue
    if (entry.isDirectory()) {
      files.push(...await listFilesRecursively(join(dir, entry.name), relPath))
    } else {
      files.push(relPath)
    }
  }
  return files
}

async function scanSkills(skillsDir: string): Promise<SkillEntry[]> {
  const catalog: SkillEntry[] = []
  const entries = await readdir(skillsDir, { withFileTypes: true })

  for (const entry of entries) {
    if (!entry.isDirectory()) continue

    const skillDir = join(skillsDir, entry.name)
    const skillMdPath = join(skillDir, 'SKILL.md')
    if (!existsSync(skillMdPath)) continue

    const frontmatter = parseFrontmatter(await readFile(skillMdPath, 'utf-8'))
    if (!frontmatter?.description) {
      log.warn(`Skipping skill "${entry.name}": missing description in SKILL.md frontmatter`)
      continue
    }

    const name = frontmatter.name || entry.name
    if (!validateSkillName(name, entry.name)) continue

    const files = await listFilesRecursively(skillDir)
    catalog.push({
      name,
      description: frontmatter.description,
      files: ['SKILL.md', ...files.filter(f => f !== 'SKILL.md')]
    })
  }

  return catalog.sort((a, b) => a.name.localeCompare(b.name))
}
