# 大纲 JSON Schema

`build_pptx.py` 读取的结构。所有文本纯中文。

```jsonc
{
  "template": "员工安全知识培训",   // 模板键或别名，见 templates/registry.json
  "title": "动火作业安全培训",       // 用于默认文件名
  "pages": [ /* 每页一个对象 */ ]
}
```

## 页对象

```jsonc
{
  "src": 5,              // 必填：克隆模板的第几页（由 make_outline.py 生成，不要手改）
  "kind": "content",     // cover | toc | section | content | table | closing
  "title": "风险辨识",
  "subtitle": "…",       // 仅 cover / 部分 closing
  "caps": {              // 该页各类槽位的字数上限，由脚本按版式实测写入
    "title": 9, "item_head": 8, "item_body": 55
  },
  "items": [             // 条数由模板该页的容量决定，不得增删
    { "head": "可燃气体积聚", "body": "受限空间易积聚可燃气体，动火前须气体检测" }
  ]
}
```

| kind | 用到的字段 |
|------|-----------|
| `cover` | `title`、`subtitle`、`items[].body`（署名槽：汇报人 / 汇报单位 / 日期） |
| `toc` | `items[].head`（各章节名） |
| `section` | `title`（序号由脚本自动写） |
| `content` | `title`（页眉，通常是章节名）、`items[].head` + `items[].body` |
| `closing` | `title`、`subtitle`、`items[].body`（署名槽） |

封面/封底的 `items` 占位会带上模板原文提示，如 `{"body": "{汇报人：待用名}"}`——按实际署名信息替换。

## 硬约束

- **`src` 不可臆造**：必须是 `make_outline.py` 排布出来的模板页号，否则渲染直接报错。
- **`items` 条数不可增删**：骨架给几条就写几条。多写的会被丢弃，少写的会留下空框。
- **字数不可超 `caps`**：这是按文本框实际宽高与字号实测出来的容量，不是建议值。
  超了不是「稍微挤一点」——15 字的标题塞进 6 字的封面框会挤成三行盖住整个版面。
- 需要更多要点时不要往一页里塞，改为增加页数让 `make_outline.py` 重新排布。
- 渲染后必须跑 `check_pptx.py`，它会按同一套 `cap` 判据把溢出全部揪出来。

## 渲染行为

- 每页都是模板对应源页的深拷贝，同一源页可被多次克隆。
- `decor` 槽（LOGO、PART 01、条目编号）保持原样；`no` 槽仅在 `section` 页被改写为章节序号。
- 没有对应内容的可填槽位会被清空——宁可留空框，也不把「单击此处添加文本」带进成品。
