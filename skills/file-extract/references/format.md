# 输出格式与失败分类

`scripts/extract.py` 的 stdout 是一份 JSON，stderr 是进度日志。用管道解析时不要 `2>&1`。

## 顶层结构

```json
{
  "source": { "kind": "url", "value": "https://...", "bytes": 1309984, "archive": true },
  "files": [ ... ],
  "skipped": [ { "path": "x/.DS_Store", "reason": "system_noise" } ],
  "pending_ocr": [ { "path": "签约依据/会议纪要.png", "ext": "png", "url": "https://.../api/image/xxx", "uploaded": true, "detail": "已上传，待下游视觉模型识别" } ],
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
| `ocr` | 该条文本是否由 OCR 或平台解析得到（`true` 时可能有错字） |
| `text` | 抽取到的正文 |
| `pages` / `pages_read` | 仅 PDF：总页数与实际读取页数 |

docx 的段落与表格按文档原始顺序抽取，表格行渲染成 `单元格 | 单元格` 一行——合同大量条款排在表格里，只取段落会整段丢失。未填写的内容控件（Word 里显示为「单击此处输入文字。」）整块丢弃，不当成正文。

## `pending_ocr[]`

本机识别不了、脚本内也没能解决的图片与扫描件。它不是错误，而是「这份材料还需要一步才能读到」。

| 字段 | 说明 |
| --- | --- |
| `path` | 压缩包内的相对路径 |
| `ext` | 小写扩展名 |
| `url` | 上传到平台后可匿名访问的完整地址；没上传成功时为空 |
| `uploaded` | 是否已传回平台。`true` 时交给下游视觉模型识别，`false` 时只能请用户单独提供 |
| `detail` | 未能在脚本内解决的原因 |

调用方必须把这一段如实转达给用户，不能因为它不在 `errors` 里就当成材料齐备。

## `errors[].kind`

| kind | 含义 | 是否整体失败 |
| --- | --- | --- |
| `download_failed` | URL 取不到：网络不可达、超时、状态码非 200 | 是 |
| `too_large` | 下载体积或解压后累计体积超限 | 是（顶层）/ 否（某条目触顶） |
| `bad_archive` | 不是有效压缩包，或压缩包损坏 | 是（顶层）/ 否（嵌套包） |
| `unsupported_ext` | 扩展名不在支持范围（`doc`、`xlsx`、`ppt` 等） | 否 |
| `parse_failed` | 单个文件抽取失败：文件损坏、PDF 加密等 | 否 |
| `empty_text` | 抽出内容接近空 | 否 |

`skipped[].reason`：`system_noise`（`__MACOSX/`、`.DS_Store`、`~$` 锁文件、`.` 开头的隐藏文件）、`entry_limit`（条目数触顶）、`nesting_limit`（嵌套层数触顶）。这三类不需要向用户解释。

## 依赖与环境

主链路（`extract.py` / `archive.py` / `readers.py` / `platform_api.py`）零第三方依赖，离线环境直接 `python3` 跑。

| 用途 | 库 | 说明 |
| --- | --- | --- |
| 解压 | 标准库 `zipfile` | 不落盘，条目留在内存 |
| docx 抽文本 | 标准库 `zipfile` + `re` | 直接解析 `word/document.xml`，段落与表格按原文次序 |
| PDF 抽文本 | `pypdf` | 纯 Python。优先用环境里已装的，没有就从 `vendor/pypdf-*.whl` 直接 zipimport |
| 平台上传与解析 | 标准库 `urllib` | 手写 multipart，见 `scripts/platform_api.py` |
| 本机 OCR | `rapidocr-onnxruntime` 1.4.x | 独立入口 `scripts/ocr.py`，约 150MB，需联网现装 |
| 扫描件栅格化 | `pypdfium2` | 同上，仅本机 OCR 这条路用得到 |

选 `rapidocr-onnxruntime` 1.4.x 而不是新版 `rapidocr` 3.x：后者运行时才去 ModelScope 拉模型，沙箱每轮全新会重复下载，多一个必失败点。它要求 Python < 3.13。

`vendor/` 下放的是 wheel 而不是解开的源码：wheel 本身就是 zip，纯 Python 包放进 `sys.path` 就能直接 import，一个文件即可，不污染仓库。

已知环境风险：本机 OCR 那条路要求能联网装包，且 `rapidocr-onnxruntime` 硬依赖 `opencv-python`（非 headless），精简镜像上可能缺 `libGL.so.1`。离线沙箱里这条路必然不可用，图片改走平台识别，其余材料不受影响。

## 实测结论

- 中石化合同类 PDF（10~15 页、150~230KB）有完整文本层，`pypdf` 直接抽取，中文与金额均正确。
- 同一批材料里的审批表、询比价表是扫描件（无文本层），走栅格化 + OCR 能正确读出金额与单位名称（实测「预算 31.2 万元 / 中标价 24.96 万元」全部命中）。
- 会议纪要截图 OCR 结果可用于业务判断，但印章、手写签名区域会产生零散错字，引用前需与上下文互证。
