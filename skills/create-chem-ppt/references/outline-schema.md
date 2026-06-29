# 大纲 JSON Schema

`build_pptx.py` 读取的结构。所有文本纯中文。

```jsonc
{
  "template": "危化品管理",        // 五选一：项目汇报|安全生产培训|政策宣贯|危化品管理|项目申报
  "title": "演示文稿标题",          // 用于默认文件名
  "base": "path/模板.pptx",          // 可选：以现有 PPT 为母版，继承其版式
  "logo": "assets/logo.png",        // 可选：封面右上插入；页面可用 page.logo 覆盖
  "pages": [ /* 每页一个对象，type 决定版式 */ ]
}
```

`base`/`logo` 优先级：CLI(`--base/--logo`) > 大纲顶层 > 主题默认。`base` 为空时用内置版式。

## 页面类型

| type | 必填字段 | 可选 | 说明 |
|------|---------|------|------|
| `cover` | `title` | `subtitle`、`org` | 全屏主色封面 |
| `toc` | `items[]` | — | 目录，自动 01/02 编号 |
| `section` | `title` | `subtitle` | 章节分隔页 |
| `points` | `title`、`points[]` | — | 要点页；point 可为字符串或 `{head,body}` |
| `table` | `title`、`rows[][]` | — | 首行为表头 |
| `warning` | `title`、`points[]` | — | 安全/危险提示红框 |
| `closing` | — | `title`、`org` | 封底致谢 |

## 约束

- `points` 单页 ≤ 6 条，`rows` ≤ 8 行；超出请拆页。
- 不含 `cover/closing/toc/section` 的内容页才计入正文页数。
- 字段缺失时 `points` 为兜底版式。
