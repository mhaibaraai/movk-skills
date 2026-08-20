/** 技能文件内容端点，路径形如 /.well-known/skills/<技能名>/<文件相对路径> */
export default defineEventHandler(async (event) => {
  const raw = getRouterParam(event, 'path', { decode: true })
  const [name, ...rest] = (raw || '').split('/')
  const path = rest.join('/')

  if (!name || !path) {
    throw createError({ statusCode: 400, message: 'Expected /.well-known/skills/<skill>/<file>' })
  }

  const content = await readSkillFile(event, name, path)

  setResponseHeader(event, 'content-type', skillFileMimeType(path))
  setResponseHeader(event, 'cache-control', 'public, max-age=3600')

  return content
})
