---
name: file-extract
description: 文件解包、文本抽取与排版样式画像基座。给一个 URL 或本地路径，返回压缩包内全部材料的结构化文本——zip 递归解压（修复中文文件名乱码）、PDF 抽文本、Word（docx）连表格一起抽，图片与扫描件本机联网时走 rapidocr、离线时交回平台识别。脚本零第三方依赖，离线环境直接可跑。另可产出排版样式画像（字体、字号、行距、缩进、页边距）并比对两份文件的差异。单个文件解析失败不影响其余材料，失败原因逐条如实报出。当用户提到压缩包解析、zip 解压、批量文件提取、PDF 文本提取、Word 文本提取、扫描件识别、图片 OCR、排版格式对比、字体字号提取、附件材料整理时触发。
---

# 文件解包与抽取基座

把「一个压缩包 / 一个文件」变成模型可读的结构化文本材料。设计动机：宿主环境的解析节点只吃单独上传的文件，压缩包内部的材料进不去；沙箱每轮全新，也不能依赖上一轮留下的中间产物——解包与抽取必须内化进脚本本身，一次调用拿全。

运行约定：

- 路径一律相对本技能根目录：本技能脚本写 `scripts/x.py`；被其他技能调用时它们用 `../file-extract/` 前缀。执行时给命令加上技能根目录前缀，不要 `cd` 进技能目录——输出文件要落在当前工作目录。前缀取宿主加载技能时告知的 Base directory；拿不到就用 `find / -name extract.py -not -path '*__pycache__*' 2>/dev/null | head -1` 定位，取其上两级为技能根目录。
- 主链路零第三方依赖，直接用 `python3` 调用。只有本机 OCR（`scripts/ocr.py`）还需要 `uv run` 装依赖，离线环境装不上是正常的，见下文 OCR 一节。
- `uv run` 命令不要加 timeout 参数，沙箱后端不支持 per-command timeout override，加了必定报错。

## 工作流程

### Step 1：抽取

```bash
python3 scripts/extract.py --url https://<域名>/api/file/<id>
python3 scripts/extract.py --path ./材料.zip --ocr off
```

`--url` 与 `--path` 二选一。压缩包会递归展开（嵌套深度 2 层），非压缩包按单文件处理。**一次调用处理完整个包**，不要逐个文件反复调——OCR 依赖首次加载有固定开销，拆开调是在重复付费。

支持的扩展名：文档 `pdf` / `docx` / `txt` / `md` / `csv`，图片 `jpg` / `jpeg` / `png` / `gif` / `bmp` / `webp` / `tif` / `tiff`。其余（`doc`、`xlsx`、`ppt` 等）进 `errors` 的 `unsupported_ext`，不静默丢弃。

常用参数：`--ocr auto|on|off`、`--max-chars`（单文件文本上限，默认 40000）、`--max-bytes`（下载体积上限，默认 50MB）、`--out-dir`（把解出的原始文件落盘，供后续处理）。

### Step 2：读结果

输出 JSON 分五段：`source`（来源与体积）、`files`（成功抽到文本的材料）、`skipped`（系统噪音，无需关心）、`pending_ocr`（待识别的图片与扫描件）、`errors`（逐条失败原因）。字段全表与 `errors[].kind` 取值见 [references/format.md](references/format.md)。

**`pending_ocr` 与 `errors` 必须读、必须如实转达给用户**，它们是「这份材料我没看到」的唯一凭据。把没抽到的文件当成不存在，会让下游拿着残缺材料下结论。

## 排版样式画像

纯文本里没有字体字号，排版审查只能从原始文件取。独立入口 `scripts/styles.py` 把一份 docx / pdf 归纳成一份样式画像，给了 `--template` 就顺带算出两者的差异：

```bash
python3 scripts/styles.py --path 合同.pdf --template 标准模板.docx
```

输出 `contract`、`template` 两份画像（正文字体字号、行距、首行缩进、页边距、页面尺寸、按字号推断的疑似标题、顶层条款序号连续性）、`diffs`（逐项比对，`match` 为 `false` 的才是问题，带容差与口径 `note`）、`not_comparable`（这次核不了的项及原因）。

两条来源的能力不同，**`not_comparable` 必须如实转达**：docx 的样式是精确读出来的；PDF 只有字体、字号是直读，行距靠相邻基线差实测，左/上边距按文字起始位置推算，首行缩进、对齐、右/下边距根本取不到。

## OCR

图片没有别的路可走，一律走 OCR；PDF 只在**抽不出文本层**（疑似扫描件）时才回落识别，有文本层的直接用文本层，快且准。

识别有两条路，按环境自动选：

1. **本机 OCR**：独立入口 `scripts/ocr.py`（rapidocr，约 150MB 依赖，由 `uv run` 现装）。只在真的遇到图片或扫描件时才被拉起，`--ocr off` 的运行完全不碰它。模型权重打包在 wheel 内，运行时不联网拉模型。
2. **平台识别**：本机这条路不可用（离线环境装不上依赖）时，给了平台参数就把材料交回平台——

```bash
python3 scripts/extract.py --path ./材料.zip \
  --platform-base https://<域名> --platform-token <token>
```

扫描件 PDF 由平台直接解析成正文写回 `files`；图片上传后换成公开 URL 放进 `pending_ocr`，由调用方的下游节点交给视觉模型识别。两条路都走不通的材料留在 `pending_ocr` 里，`uploaded` 为 `false`。

`--parse-remote force` 让文档也走平台解析，`off` 则完全不出网。token 只用于请求头，不会出现在任何输出里。

**部署到新环境先探一次**：

```bash
python3 scripts/extract.py --check-env
```

输出 `docx` / `pypdf` / `ocr` 三项可用性。离线环境里 `ocr` 必然不可用，图片走平台那条路——这是设计好的降级，不是故障。

## 质量要求

- 如实反馈：`errors` 与 `skipped` 原样转述，不猜测失败原因、不替用户美化
- 不臆造内容：OCR 结果可能有错字（印章、手写签名尤其容易误识），引用关键数字前先与上下文互相印证
- 一次取全：一次调用处理整个包，不逐文件重复调用

## 特殊处理

- **中文文件名乱码**：Windows / 部分打包工具写的 zip 不置 UTF-8 标志位，脚本已自动按 GBK 回落修复，无需调用方干预
- **扫描件 PDF**：`--ocr off` 时进 `pending_ocr`；`auto` 时先试本机栅格化 OCR，再试平台解析，都不成会回退到文本层那点残留内容并在 detail 里注明
- **超大包**：下载体积、解压后累计体积（200MB）、条目数（500）三重上限，触顶时报 `too_large` 并停在该条目，已处理的材料照常返回
- **加密 PDF**：仅限制编辑的加密仍可读；真正设了打开密码的报 `parse_failed`（解密要 `cryptography`，零依赖环境装不了）
- **整体失败只有三种**：`download_failed`（取不到）、`too_large`（超限）、`bad_archive`（不是有效压缩包）。此时脚本退出码非零，stdout 是 `{"error": {...}}`；其余失败都是单文件级别，不中断其余材料
