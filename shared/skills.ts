/** 构建期扫描 skills/ 得到的单个技能条目 */
export interface SkillEntry {
  /** 技能名，与目录名一致 */
  name: string
  /** SKILL.md frontmatter 的 description */
  description: string
  /** 技能目录内可对外分发的文件相对路径，SKILL.md 恒为首项 */
  files: string[]
}
