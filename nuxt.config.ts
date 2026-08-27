export default defineNuxtConfig({
  modules: [
    '@nuxtjs/mcp-toolkit',
    '@nuxt/eslint',
    '@vercel/analytics'
  ],

  devtools: { enabled: true },

  runtimeConfig: {
    mcpDemoToken: ''
  },

  experimental: {
    asyncContext: true
  },

  compatibilityDate: '2026-06-30',

  nitro: {
    sourceMap: false
  },

  telemetry: false,

  eslint: {
    config: {
      stylistic: {
        commaDangle: 'never',
        braceStyle: '1tbs'
      }
    }
  },

  mcp: {
    name: 'Movk Skills',
    description: 'Agent Skills 库与业务 MCP 服务',
    instructions: [
      '本服务托管一组 Agent Skills。先用 list-skills 浏览清单，',
      '再用 get-skill 读取某个技能的 SKILL.md 正文并按其工作流执行；',
      '技能内的脚本与参考文档用 read-skill-file 按需读取，不要一次性全读。',
      '业务工具位于 /mcp/<业务名> 子端点，需各自的 Bearer token。'
    ].join(''),
    browserRedirect: '/'
  }
})
