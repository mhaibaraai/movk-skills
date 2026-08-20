# 输出格式与失败分类

`scripts/extract.py` 的 stdout 是一份 JSON，stderr 是进度日志。用管道解析时不要 `2>&1`。

## 顶层结构

```json
{
  "source": { "kind": "url", "value": "https://...", "bytes": 1309984, "archive": true },
  "files": [ ... ],
  "skipped": [ { "path": "x/.DS_Store", "reason": "system_noise" } ],
  "errors": [ { "path": "清单.xlsx", "kind": "unsupported_ext", "detail": "不支持的扩展名 .xlsx" } ]
}
```

整体失败时 stdout 只有一段 `{"error": {"kind": ..., "detail": ...}}`，退出码为 1。

## `files[]`

| 字段 | 说明 |
| --- | --- |
| `path` | 压缩包内的相对路径，已修复中文文件名乱码；嵌套包内的条目形如 `外层.zip/内层文件.pdf` |
| `ext` | 小写扩展名 |
| `kind` | `document` 或 `image` |
| `chars` | `text` 的字符数 |
| `truncated` | 是否因 `--max-chars` 被截断 |
| `ocr` | 该条文本是否由 OCR 得到（`true` 时可能有错字） |
| `text` | 抽取到的正文 |
| `pages` / `pages_read` | 仅 PDF：总页数与实际读取页数 |

docx 的段落与表格按文档原始顺序抽取，表格行渲染成 `单元格 | 单元格` 一行——合同大量条款排在表格里，只取段落会整段丢失。

## `errors[].kind`

| kind | 含义 | 是否整体失败 |
| --- | --- | --- |
| `download_failed` | URL 取不到：网络不可达、超时、状态码非 200 | 是 |
| `too_large` | 下载体积或解压后累计体积超限 | 是（顶层）/ 否（某条目触顶） |
| `bad_archive` | 不是有效压缩包，或压缩包损坏 | 是（顶层）/ 否（嵌套包） |
| `unsupported_ext` | 扩展名不在支持范围（`doc`、`xlsx`、`ppt` 等） | 否 |
| `parse_failed` | 单个文件抽取失败：文件损坏、PDF 加密等 | 否 |
| `empty_text` | 抽出内容接近空：PDF 无文本层且未启用 OCR，或 OCR 没识别出文字 | 否 |
| `ocr_unavailable` | OCR 被 `--ocr off` 关闭，或 rapidocr / opencv 依赖不可用 | 否 |
| `ocr_failed` | 单个文件的 OCR 执行失败 | 否 |

`skipped[].reason`：`system_noise`（`__MACOSX/`、`.DS_Store`、`~$` 锁文件、`.` 开头的隐藏文件）、`entry_limit`（条目数触顶）、`nesting_limit`（嵌套层数触顶）。这三类不需要向用户解释。

## 依赖与环境

| 用途 | 库 | 说明 |
| --- | --- | --- |
| 解压 | 标准库 `zipfile` | 不落盘，条目留在内存 |
| PDF 抽文本 | `pypdf` | 纯 Python |
| docx 抽文本 | `python-docx` | 纯 Python |
| OCR | `rapidocr-onnxruntime` 1.4.x | 模型打包在 wheel 内，运行时不联网拉模型 |
| 扫描件栅格化 | `pypdfium2` | 自包含 wheel，不依赖系统 poppler |

选 `rapidocr-onnxruntime` 1.4.x 而不是新版 `rapidocr` 3.x：后者运行时才去 ModelScope 拉模型，沙箱每轮全新会重复下载，多一个必失败点。它要求 Python < 3.13。

已知环境风险：`rapidocr-onnxruntime` 硬依赖 `opencv-python`（非 headless），精简镜像上可能缺 `libGL.so.1`，此时 `--check-env` 的 `ocr.rapidocr` 为 `false` 并给出 detail。图片材料随即降级为 `ocr_unavailable`，其余材料不受影响。

## 实测结论

- 中石化合同类 PDF（10~15 页、150~230KB）有完整文本层，`pypdf` 直接抽取，中文与金额均正确。
- 同一批材料里的审批表、询比价表是扫描件（无文本层），走栅格化 + OCR 能正确读出金额与单位名称（实测「预算 31.2 万元 / 中标价 24.96 万元」全部命中）。
- 会议纪要截图 OCR 结果可用于业务判断，但印章、手写签名区域会产生零散错字，引用前需与上下文互证。
