# 直接文本/大纲路径测试素材

用于测试 create-ppt 的「直接文本/大纲路径」（上传文档或粘贴文稿 → 跳过意图门 → 直奔大纲门）。三份素材主题各异、映射不同模板、覆盖三种上传类型。**内容均为虚构示例**（示例公司、示例数据），仅供功能测试。

| 文件 | 类型 | 建议模板 | 用途 |
|------|------|----------|------|
| `年终安全工作汇报.txt` | 纯文本 | `年终安全工作汇报模版` | 测「粘贴一段文稿」或 txt 上传；平台侧走「文档提取」 |
| `智慧仓储升级改造项目立项申请.docx` | Word | `项目立项汇报模版` | 测 docx 上传；平台侧走「文档提取」 |
| `危化品动火作业安全专项培训.pdf` | PDF | `员工安全知识培训` | 测 PDF 上传；平台侧走「PDF文档解析」或「文档提取」 |

## 本地端到端跑法（对话式，不经平台）

以 PDF 素材为例，先抽正文当素材，再直奔大纲门：

```bash
cd skills/create-ppt
# 1) 抽取素材正文（平台侧由解析节点完成，本地用 pandoc/pdftotext 模拟）
pandoc tests/fixtures/智慧仓储升级改造项目立项申请.docx -t plain -o /tmp/src.txt

# 2) 以素材内容为基础生成大纲骨架并填充（详见 SKILL.md 门②）
uv run scripts/make_outline.py --template 项目立项汇报模版 --pages 14 \
  --title "智慧仓储升级改造项目立项" \
  --sections "项目背景,建设目标,建设内容,投资与进度,预期效益,风险对策" > outline.json
uv run scripts/make_outline.py --preview outline.json

# 3) 渲染 + 自检
uv run scripts/build_pptx.py --outline outline.json --out 智慧仓储升级改造项目立项.pptx
uv run scripts/check_pptx.py --outline outline.json 智慧仓储升级改造项目立项.pptx
```

## 平台侧上传测试

上传对应文件到起始「文件上传」组件，选定表中「建议模板」，直接发一句「按这份材料做 PPT」，验证：文件经解析节点落 `g_source` → 主对话跳过意图门直奔大纲门。txt/docx 走「文档提取」、pdf 可验证「PDF文档解析」分支。

## 重新生成

```bash
# docx（pandoc 原生，无需 LaTeX）
pandoc source.md -o output.docx
# pdf（先转 docx 再用 LibreOffice 无头转 pdf，中文可正常抽取）
pandoc source.md -o /tmp/x.docx && soffice --headless --convert-to pdf --outdir . /tmp/x.docx
```
