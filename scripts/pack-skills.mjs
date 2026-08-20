import { existsSync } from 'node:fs'
import { mkdir, readdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { zipSync } from 'fflate'
import { listSkillFiles } from './skill-files.mjs'

/**
 * 把 skills/ 下的技能打成 zip，供离线上传场景使用。
 * 打包范围与构建期白名单同源，见 skill-files.mjs。
 *
 * 用法：node scripts/pack-skills.mjs [技能名...]
 */

const rootDir = join(dirname(fileURLToPath(import.meta.url)), '..')
const skillsDir = join(rootDir, 'skills')
const outDir = join(rootDir, 'dist/skills')

const requested = process.argv.slice(2)
const entries = await readdir(skillsDir, { withFileTypes: true })
const names = entries
  .filter(entry => entry.isDirectory())
  .map(entry => entry.name)
  .filter(name => !requested.length || requested.includes(name))
  .sort()

for (const missing of requested.filter(name => !names.includes(name))) {
  console.error(`跳过 ${missing}：skills/ 下没有这个目录`)
}

await mkdir(outDir, { recursive: true })

let packed = 0
for (const name of names) {
  const skillDir = join(skillsDir, name)
  if (!existsSync(join(skillDir, 'SKILL.md'))) {
    console.error(`跳过 ${name}：缺少 SKILL.md`)
    continue
  }

  const files = await listSkillFiles(skillDir)
  /** @type {Record<string, Uint8Array>} */
  const payload = {}
  for (const file of files) {
    payload[`${name}/${file}`] = await readFile(join(skillDir, file))
  }

  const outPath = join(outDir, `${name}.zip`)
  await writeFile(outPath, zipSync(payload, { level: 9 }))
  console.log(`${name} -> dist/skills/${name}.zip (${files.length} 个文件)`)
  packed++
}

console.log(`共打包 ${packed} 个技能`)
