# create-ppt 重构：模板复用 + WPS 式两道确认门

> 技能已由 `create-chem-ppt` 重命名为 `create-ppt`（企业汇报/培训通用定位），文件夹 `skills/create-ppt/`。
> §1–4 的克隆回填内核已实现落地；本轮新增：第 5 套「通用模板」、起始「用户输入」表单（单选模板 + 文件上传）、两条入口分支、分页卡片预览、平台编排定案方案 A。

## Context

目标是让智能体达到 WPS AI 的体验：**用户输入（选模板 + 可上传文档）→ 确认意图 → 生成大纲 → 确认 → 执行生成**。最初两个根本缺口已解决：

1. **产出不像模板（已解决）。** 模板是完整设计稿：每页由自由形状、装饰图片、文本框拼成，几乎不用占位符。旧 `build_pptx.py` 在空白版式上**程序化画色块**，模板设计被整个丢弃。现改为**克隆模板设计页 + 文本回填**，产出与模板视觉一致。
2. **没有确认门（已解决）。** 现按两道确认门推进，禁止从一句话直奔渲染。

已验证的关键结论：把模板页**深拷贝 + rId 重映射**克隆到同一 package 内、再回填文本，图片/主题/字体全部完好，未引用 media 在 save 时自动丢弃（通用模板 40MB → 产物约 19MB）。

本轮新增决策：
- **第 5 套「通用模板」**：来自 `assets/PPT制作培训与模板.pptx`（95 页讲解+模板混排稿），精修出 55 页可复用集（封面 1 + 目录 1 + 章节 4 + 内容 48 + 封底 1），供任意主题兜底。
- **起始表单**：对话开始用「用户输入」组件收集 单选模板（4 专用 + 通用）+ 文件上传（仅文档，不支持 ppt 作模板）+ 主题文本框。
- **模板以单选为准**；推断意图与单选冲突时向用户确认是否切换。
- **两条入口**：上传文档/粘贴成段文稿/大纲 → 跳过意图门直奔大纲门；仅一句话主题 → 走意图门。
- **大纲确认改分页 Markdown 卡片**（图三风格），取代扁平单行。
- **平台编排定案方案 A（循环节点）**，循环次数取大值（999）。

目标产物：技能自身在任意环境可跑（对话内两道确认门），并给出平台工作流编排方案还原截图体验。

## 一、技术底座：克隆回填替代程序化绘制

### 已验证的克隆内核

在同一个 `Presentation` 对象内操作（源页与目标页同 package，无需跨包搬 media）：

```python
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

def clone(prs, i):
    src = prs.slides[i]
    new = prs.slides.add_slide(src.slide_layout)
    for sh in list(new.shapes):                      # 清掉新页自带占位符
        sh._element.getparent().remove(sh._element)
    mapping = {}
    for el in src.shapes._spTree:
        if el.tag.endswith(('}nvGrpSpPr', '}grpSpPr')):
            continue
        cp = copy.deepcopy(el)
        for node in cp.iter():                       # r:embed / r:id 重映射到新页 rels
            for attr, old in list(node.attrib.items()):
                if attr.startswith(R):
                    if old not in mapping:
                        rel = src.part.rels[old]
                        mapping[old] = (new.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
                                        if rel.is_external else
                                        new.part.relate_to(rel.target_part, rel.reltype))
                    node.set(attr, mapping[old])
        new.shapes._spTree.append(cp)
    return new
```

流程：打开模板 → 按大纲克隆所需页（可重复引用同一源页）→ 删除全部原始页 → 回填文本 → 保存。

**踩过的坑（勿走回头路）**：不要用 `rels._add_relationship(reltype, target, rid)` 保留原 rId——与新页自带的 layout rel 冲突，`save()` 时在 `target_ref` 断言崩溃。必须重映射 rId。

### 槽位定位

模板里 shape name 大量重复（`任意多边形: 形状 85` 出现多次），**必须用 shape id 定位**，不能用 name。组合（GROUP）内的文本框需递归遍历。

## 二、模板体系：以 5 个真实 pptx 为准

`templates/themes.json` 废弃，改为 `templates/registry.json`（现 5 条）：

```jsonc
{
  "员工安全知识培训":   { "aliases": ["安全生产培训", "安全培训", "危化品管理", "HSE 培训"], ... },
  "项目立项汇报模版":   { "aliases": ["项目申报", "立项", "课题答辩"], ... },
  "年终安全工作汇报模版": { "aliases": ["年终总结", "工作汇报", "安全汇报"], ... },
  "重大事件专项汇报":   { "aliases": ["专项汇报", "事故汇报", "应急事件"], ... },
  "通用模板": {
    "source": "assets/PPT制作培训与模板.pptx",
    "aliases": ["通用", "自定义", "其他", "不限", "任意主题"],
    "sections": ["背景与目标", "现状分析", "实施方案", "计划安排", "总结展望"],
    "index": "templates/index/通用模板.json"
  }
}
```

`aliases` 承接场景别名，保证「政策宣贯」「危化品管理」「通用」这类说法仍能落到某个真实模板。用户自带模板时现场跑索引脚本即可注册。

**通用模板的精修**：原始稿 95 页是「PPT制作规范与模板」讲解+版式混排。用 `index_template.py` 出草稿后，脚本化精修（`scripts` 外的一次性工具）保留可复用页、丢弃规范讲解页：
- 封面 `i=0`、目录 `i=1`、封底 `i=94` 直接留用；
- `01./02./03./04.` 四张数字分隔页（`i=2,4,15,22`，原判 content）重标为 `section`：数字槽转 `no`（按章节序号自动改写、支持 >4 章循环取号）、小标题槽转 `title`；
- 内容页只取 `i∈[23,92]` 且 `2≤items≤6` 的 48 张（过密页与规范讲解页排除）。
最终 55 页（封面 1 / 目录 1 / 章节 4 / 内容 48 / 封底 1）。注意：`index` 里的 `i` 必须保留原始页序（`build_pptx` 按它 `clone_slide(prs, i)`），精修只删条目不重排号。

### 页型索引 `templates/index/<模板>.json`

每个模板页 = 一个「版式样例」，索引它的页型与文本槽位：

```jsonc
{
  "template": "员工安全知识培训",
  "source": "assets/员工安全知识培训.pptx",
  "slides": [
    { "i": 0, "kind": "cover",
      "slots": [ {"sid": 7, "role": "title", "cap": 14},
                 {"sid": 5, "role": "subtitle", "cap": 10},
                 {"sid": 9, "role": "decor"} ] },
    { "i": 1, "kind": "toc", "items": 4, "slots": [...] },
    { "i": 2, "kind": "section", "slots": [ {"sid": 3, "role": "no", "auto": "序号"},
                                            {"sid": 4, "role": "title", "cap": 12} ] },
    { "i": 5, "kind": "content", "items": 4,
      "slots": [ {"sid": 12, "role": "item_head", "idx": 0, "cap": 6},
                 {"sid": 13, "role": "item_body", "idx": 0, "cap": 40}, ... ] }
  ]
}
```

- `kind`：`cover | toc | section | content | table | warning | closing`
- `role`：`title | subtitle | item_head | item_body | no | decor`
- `decor`（`01`、`PART 01`、`PROJECT APPLICATION`、`LOGO`）**保留原样不回填**；`no` 类按章节序号自动改写。
- `cap`：字符容量，取自原占位文本长度并按角色收敛，作为写作硬约束（防溢出，这是模板不崩版的关键）。
- `items`：该页可容纳的要点数——**大纲反过来适配模板**，只生成模板存在的 items 数，避免运行时删形状破坏设计。

索引由 `scripts/index_template.py` 自动出草稿（按 z-order/字号/位置/文本长度启发式猜 kind 与 role），再由模型精修一次落盘。**4 个模板共 ~120 页，属一次性离线工作**，运行时不再需要模型读模板。

## 三、交互：入口分流 + 两道确认门

### 入口分流（决定走几道门）

- **直接文本/大纲路径**：用户上传了文档，或粘贴了成段文稿/已有大纲 → **跳过门 ①**，用选定模板生成骨架、以其内容填充优化，直奔门 ②。
- **主题路径**：只给一句话主题 → 走门 ①。
- **模板锁定**：模板一律以用户选定值（起始单选）为准，不由措辞改判。模型仍从主题推断场景；若与选定模板明显不符（选了安全培训、主题却像年终总结），在意图卡追加一行「您选了 X 模板，但主题更像 Y，是否切换？」，等用户确认。

### 门 ① 意图确认卡（仅主题路径）

抽取 5 字段，选用模板填选定值，其余缺失给默认值并**明示**，以卡片复述、等用户确认：

```text
1. 制作目标：动火作业安全培训
2. 目标受众：一线操作工与监护人
3. 选用模板：员工安全知识培训   ← 起始单选，锁定
4. 页数规模：高度概括（12–16 页）
5. 补充说明：需含事故案例与作业票证要求
```

### 门 ② 分页大纲确认（分页 Markdown 卡片）

生成 `outline.json` 后，用 `make_outline.py --preview` 输出**分页 Markdown 卡片**（脚本产出，不靠模型复述，省 token 且稳定；「指定回复」节点直接渲染成图三效果）：

```text
**动火作业安全培训**　模板：员工安全知识培训　共 14 页

---

**第 1 页**　`封面`
# 动火作业安全培训
一线操作与监护人员必修

---

**第 3 页**　`章节`
### 01 · 风险辨识

---

**第 4 页**　`要点`　风险辨识
- **四大风险源** — 可燃气体 / 受限空间 / 动火飞溅 / 误操作
- **辨识方法** — 作业前 JSA 危害分析
```

用户确认后才渲染。改动只需重跑填充步，不重来。

## 四、脚本与文档现状（均已落地）

| 文件 | 状态 |
|------|------|
| `scripts/index_template.py` | 模板 pptx → 页型/槽位索引草稿 JSON（递归遍历 GROUP，按 shape id 定位） |
| `scripts/build_pptx.py` | 「克隆 + 回填」：读 registry + index + outline，克隆源页、按 sid 回填、删原始页、保存 |
| `scripts/make_outline.py` | 读 index 的可用页型与 items 容量排布；`--preview` 输出**分页 Markdown 卡片** |
| `scripts/check_pptx.py` | 成品自检：残留占位与文本溢出（判据取自 index 的 cap，不依赖渲染器） |
| `scripts/inspect_template.py` | 校验产物页数/版式 |
| `scripts/pptx_util.py` | 公共底座：registry/index 加载、`clone_slide`（rId 重映射）、`set_text` 等 |
| `templates/registry.json` | 5 条（4 专用 + 通用），取代 `themes.json`（已删） |
| `templates/index/*.json` | 5 份索引（含精修后的 `通用模板.json`，55 页） |
| `SKILL.md` | 重命名 create-ppt、企业通用定位、5 套模板、入口分流 + 模板锁定 + 冲突确认、分页卡片契约 |
| `references/*` | template-catalog / outline-schema / writing-guide，随第 5 套模板与分页卡片同步 |

## 五、平台工作流编排（方案 A 定案，还原截图）

技能保持自足；平台上两道确认门用**组件**编排，而不是塞进一次 AI 对话靠模型自觉——模型会跳步、会把确认写成散文而不是可交互卡片。多轮修改用**循环节点（方案 A）**，次数填 999（当上限，用户一般改不了几轮）。

### 5.1 真实变量总线与节点能力（画布截图实证）

变量引用语法（括号内带空格，三种前缀）：
- 内置/系统全局变量：`{{ global.变量名 }}`（如 `{{ global.template }}`、`{{ global.chat_id }}`）
- 自建全局变量：`{{ 全局变量.变量名 }}`（如 `{{ 全局变量.g_outline }}`）
- 节点输出：`{{ 节点名.变量名 }}`（如 `{{ 开始.question }}`、`{{ 条件.branch_name }}`）

| 变量 | 来源 | 说明 |
|------|------|------|
| `{{ global.template }}` | 开始节点全局变量（由「用户输入」单选框产生） | **选定模板，权威**。取值为 5 个模板键之一 |
| `{{ 开始.question }}` | 开始节点输出 | 用户本轮文本消息（主题或一段话） |
| `{{ 开始.document }}` | 开始节点输出 | 上传的文件对象；文本要经「文档内容提取」节点解析 |
| `{{ global.chat_id }}` | 系统全局变量 | 会话 id，证明全局变量按会话持久（多轮可靠） |
| `{{ 全局变量.g_source }}` | 自建全局变量（string） | 上传文档解析后的正文文本 |
| `{{ 全局变量.g_intent }}` | 自建全局变量（string） | 意图 5 要素合成文本 |
| `{{ 全局变量.g_outline }}` | 自建全局变量（string） | 分页卡片预览 + ```json 大纲块 |

节点能力（据配置面板）：

| 节点 | 实证要点 |
|------|---------|
| **开始 / 用户输入** | 用户输入是一张字段表（参数 / 显示名称 / 是否必填 / 组件类型）；本流只需一个字段 `template`（组件类型=单选框）。**文件上传**是节点上的独立开关 + 齿轮设置（限文档类型、单次数量、单文件大小），产出 `{{ 开始.document }}`。这些都在循环体外，不受「表单不能进循环」限制 |
| **循环** | 类型：固定次数 / 数组循环 / 变量次数；有「循环开始」子节点（输出 `loop_item`/`loop_index`），循环节点输出 `loop_result`。**无 while、不支持 break** → 固定次数填 999 当上限，靠「条件确认分支连到循环外」当退出 |
| **AI 对话** | 可选模型：`energy-deepseek-v3` / `energy-qwen3-30b-a3b-instruct-2507` / `energy-qwen-plus` 等（生成类用前两者，判定类用 qwen-plus 够快）。有 **系统角色 + 提示词** 两个输入框；**历史聊天记录**可设（节点 / 工作流，N 轮）——判定节点靠它读到用户上一句；**Skill** 开关（挂载 create-ppt / download-link-publisher）；输出 `answer` / `reasoning_content`。❌ 无法强制调 skill（提示词约束）；❌ 文件路径不能结构化带出，outline 只能经 `answer` 文本往返；渲染与发链接必须挂**同一个 AI 对话节点** |
| **参数抽取** | 输入变量从下拉选（全局变量 / 开始）；每参数配 名称 / 字段 key / **类型（string/num/json）** / 描述；输出 `result`。类型可选 `json`，但跨节点仍走 `{{ 参数抽取.result }}` 文本 |
| **条件** | IF 变量 / 条件 / 值，操作符：为空 / 不为空 / 包含 / 不包含 / 等于 / 大于等于 / 大于…；支持 ELSE 与「添加分支」；输出 `branch_name` |
| **指定回复** | 回复类型：引用 / 自定义（自定义=直接写 Markdown，可引用变量）；输出 `answer`（回复内容） |
| **变量赋值** | 选一个变量 ← 引用变量 或 自定义值，类型 string 等 |
| **全局变量（配置）** | 变量名 + 类型（string）+ 默认值；本流新建 `g_source` / `g_intent` / `g_outline` |

另可用节点：**意图识别**（可替代 CONFIRM/REVISE 判定）、**问题优化**（可替代对粘贴文稿的预清洗）、变量聚合 / 变量拆分 / 多路召回 / 自定义函数。本方案统一用 AI 对话做判定，保持提示词可控。

### 5.2 起始节点与全局变量配置

1. **用户输入 → 字段 `template`**：显示名称「模板」、是否必填=开、组件类型=单选框，选项填 5 个模板键：`员工安全知识培训` / `项目立项汇报模版` / `年终安全工作汇报模版` / `重大事件专项汇报` / `通用模板`。产出全局变量 `{{ global.template }}`。
2. **文件上传**：开启开关 → 齿轮设置：只勾「文档」类型（TXT/MD/DOC/DOCX/HTML/CSV/XLSX/XLS/PDF），单次 1–3 个、单文件 ≤50MB。**不支持 ppt 作模板**（模板由单选决定，上传只作内容素材）。产出 `{{ 开始.document }}`。
3. **开场白**：见 SKILL.md `metadata.opening`。
4. **新建全局变量**（全局变量配置：变量名 / 类型 string / 默认值留空）：`g_source`、`g_intent`、`g_outline`。

### 5.3 拓扑（方案 A，真实变量名）

```text
[开始]  输出 {{ 开始.question }} {{ 开始.document }}；全局 {{ global.template }} {{ global.chat_id }}
  │
条件[入口分流]  IF {{ 开始.document }} 不为空  → direct
                ELSE（仅一句话主题）        → topic
  │
 ├─ direct（上传文档 / 粘贴大纲）───────────────────────
 │    文档内容提取({{ 开始.document }}) → 变量赋值 g_source
 │    AI对话[骨架+填充 stage=outline]
 │      （用 {{ global.template }} 生成骨架；以 {{ 全局变量.g_source }}/{{ 开始.question }} 为素材填充，禁止渲染）
 │    → 变量赋值 g_outline → 直接进入 循环②
 │
 └─ topic（一句话主题）─────────────────────────────
      参数抽取[意图]({{ 开始.question }}) 抽 topic/audience/scale/notes
        → 变量赋值 g_intent（模板锁定 {{ global.template }}，不推断）
      ┌ 循环① 固定次数=999（意图确认门）
      │  指定回复: 意图卡（选用模板={{ global.template }}；场景冲突时附「是否切换」确认行）
      │  AI对话[意图判定]（历史记录=工作流 1 轮）→ 首行 CONFIRM / REVISE(+合并新意图)
      │  条件({{ 意图判定.answer }} 包含 CONFIRM?)
      │     确认 → 连到循环外：AI对话[大纲]
      │     修改 → 变量赋值 g_intent=REVISE 后文本 → 下一圈
      └
      AI对话[大纲 stage=outline]({{ 全局变量.g_intent }}) → 变量赋值 g_outline
  │
┌ 循环② 固定次数=999（大纲确认门）
│  指定回复: 分页 Markdown 卡片（自定义，引用 {{ 全局变量.g_outline }} 的预览段）
│  AI对话[大纲判定]（历史记录=工作流 1 轮）→ CONFIRM / REVISE
│  条件(包含 CONFIRM?)
│     确认 → 连到循环外：AI对话[渲染]
│     修改 → AI对话[大纲修订]({{ 全局变量.g_outline }}+改法) → 变量赋值 g_outline → 下一圈
└
AI对话[渲染 stage=render]（Skill: create-ppt + download-link-publisher）→ 下载链接写进 answer
```

入口分流用**条件**（`{{ 开始.document }}` 不为空 → direct）比参数抽取更稳。若还想把「粘贴的长文稿」也走 direct，可在 topic 分支的意图抽取里加一个 `is_full_text` 字段，命中则跳去 direct 的填充节点。

「确认 → 出循环」怎么接：把条件的确认分支连到循环容器之外的下一个节点（若引擎把它当 break）；否则退化为「确认后剩余圈里 AI 对话原样回显、不再追问」，功能仍可用。

### 5.4 节点逐项配置

**条件[入口分流]** — 变量 `{{ 开始.document }}`、操作符「不为空」→ direct 分支；ELSE → topic 分支。

**文档内容提取（仅 direct）** — 输入 `{{ 开始.document }}`，输出解析文本 → 变量赋值写入 `g_source`。（文件上传设置里已注明文档需此节点解析。）

**参数抽取[意图]（仅 topic）** — 输入变量选 `{{ 开始.question }}`，参数配置（名称 / 字段 key / 类型 string / 描述）：

| 名称 | 字段 key | 描述（写进抽取提示） |
|------|---------|---------------------|
| 制作目标 | `topic` | PPT 主题，如「动火作业安全培训」 |
| 目标受众 | `audience` | 汇报/培训对象，未提及输出「公司管理层与相关业务人员」 |
| 页数规模 | `scale` | 只能取：高度概括（12–16 页）/ 标准（18–24 页）/ 详尽（25–30 页）。未提及输出高度概括 |
| 补充说明 | `notes` | 其他要求，无则输出「无」 |

模板不在此抽取——直接用 `{{ global.template }}`。抽出的 `{{ 参数抽取.result }}` 与 `{{ global.template }}` 由「变量赋值」合成 `g_intent` 文本。

**AI 对话[意图判定 / 大纲判定]** — 模型 `energy-qwen-plus`，开「历史聊天记录=工作流、1 轮」，关「返回内容」，提示词：

```text
用户上一句是对{当前待确认内容}的回复。判断其意图：
- 表示认可（确认 / 可以 / 没问题 / 就这样 / 生成吧）→ 首行只输出 CONFIRM
- 要求调整 → 首行输出 REVISE，其后另起一行给出合并修改后的完整{意图 / 大纲}
不要输出其他解释。
```

对应**条件**：变量 `{{ 判定.answer }}`、操作符「包含」、值 `CONFIRM` → 确认分支；ELSE → 修改分支。
不直接对 question 硬匹配关键词，是因为「确认一下页数改 20」这种含"确认"却是修改的句子会误判——交给模型判定更准。

**指定回复[意图卡]** — 自定义，内容引用 `{{ 全局变量.g_intent }}`；`{{ global.template }}` 与描述场景冲突时由上游在 `g_intent` 里已带上「是否切换」提示行。

**指定回复[大纲卡片]** — 自定义，直接粘 `{{ 全局变量.g_outline }}` 的预览段（分页 Markdown 卡片，平台按 Markdown 渲染成图三效果）。

topic 与 direct 各有一个独立的「大纲生成」AI 对话节点（两个分支上是两个节点实例，提示词不同），生成后都用「变量赋值」把 `{{ 该节点.answer }}` 整段写入 `g_outline`。两者公共面板：模型 `energy-deepseek-v3`、系统角色留空、**Skill 开启只挂 create-ppt**（不挂 download-link-publisher，本阶段不渲染）、开启思考关、历史聊天记录关（都是首轮生成）、返回内容关（原始 json 不直接刷给用户，由循环②的指定回复渲染干净卡片）。

**AI 对话[topic 大纲 stage=outline]** — 提示词：

```text
你是企业 PPT 生成智能体，stage=outline：只生成大纲，禁止渲染 pptx，禁止调用 download-link-publisher。

选定模板：{{ global.template }}
创作意图：
{{ 全局变量.g_intent }}

用 create-ppt 技能完成：
1. uv run scripts/make_outline.py --template {{ global.template }} --pages <规模取中值> --title <主题> --sections <章节>
2. 按 references/writing-guide.md 填充正文，严格遵守每页 caps 字数上限
3. uv run scripts/make_outline.py --preview outline.json 生成分页卡片预览

按 SKILL.md 输出契约回复：先分页卡片预览，再一个完整的 ```json 大纲块。
```

**AI 对话[direct 骨架+填充 stage=outline]** — 直接文本/大纲路径的核心节点，**跳过意图确认**，从素材直接成稿。提示词：

```text
你是企业 PPT 生成智能体，stage=outline：只生成大纲，禁止渲染 pptx，禁止调用 download-link-publisher。

选定模板（以此为准，不要改判）：{{ global.template }}
用户提供的原始素材（文档解析文本；若为空则用粘贴文本 {{ 开始.question }}）：
{{ 全局变量.g_source }}

跳过意图确认，直接按以下步骤用 create-ppt 技能产出大纲：
1. 通读素材，提炼 PPT 主题(title) 与章节(sections)；素材本身已是大纲/分点时按其结构走，不要另起炉灶。
2. 估算页数：信息量小→高度概括(12–16)，中→标准(18–24)，大→详尽(25–30)。
3. uv run scripts/make_outline.py --template {{ global.template }} --pages <页数> --title "<主题>" --sections "<章节,逗号分隔>"
4. 按 references/writing-guide.md 把每页占位换成素材里的真实内容——只做提炼与改写，不新增素材没有的数据/结论；要点条数不得增删，每条不超该页 caps。
5. uv run scripts/make_outline.py --preview outline.json 生成分页卡片预览。

按 SKILL.md 输出契约回复：先分页卡片预览，再一个完整的 ```json 大纲块。
```

direct 节点前置依赖：`文档内容提取({{ 开始.document }}) → 变量赋值 g_source`；若走的是「粘贴长文稿」而非上传文件，`g_source` 为空、提示词自动回退到 `{{ 开始.question }}`。生成后 `变量赋值 g_outline ← {{ 此节点.answer }}`，直接进入循环②大纲确认门。

**AI 对话[大纲修订]** — 提示词带 `{{ 全局变量.g_outline }}` + 用户修改意见，重出同格式（卡片 + json 块），仍 stage=outline、不渲染 → 写回 `g_outline`。

**AI 对话[渲染 stage=render]** — 模型 `energy-deepseek-v3`，开 Skill=create-ppt + download-link-publisher，提示词：

```text
stage=render：不要重新生成大纲，直接渲染。

已确认的大纲（含 json 块）：
{{ 全局变量.g_outline }}

用 create-ppt 技能完成：
1. 提取其中的 json 块，原样写入 outline.json
2. uv run scripts/build_pptx.py --outline outline.json --out <主题>.pptx
3. uv run scripts/check_pptx.py --outline outline.json <主题>.pptx 自检，报错就改短文案重渲
4. 调用 download-link-publisher 发布，回复给出下载链接

不得修改大纲内容。
```

### 5.5 技能侧契约（与对话式使用一致，不引入平台耦合）

1. **stage 语义**：`stage=outline` 只产大纲、不渲染；`stage=render` 只渲染、不改大纲。唯一传递物是 outline JSON 文本。
2. **outline 输出契约**：outline 阶段固定回复「分页 Markdown 卡片 + 一个 ```json 代码块」。AI 对话节点只有 `answer` 一个文本出口，JSON 必须能被下游原样接回、渲染阶段稳定提取。

> **仍需在画布上确认的三点**（均有兜底，不阻塞落地）：
> 1. **循环停等**：方案 A 依赖「循环体里 指定回复→AI对话 每圈会停下等用户输入」。搭最小循环（`循环(固定次数=2)→循环开始→AI对话(提示词=第 {{ 循环开始.loop_index }} 轮请随便回一句)`）点调试即可验证：发一句停等再发第二句=成立，用方案 A；一口气吐两条=不成立，退回「按消息重跑 + `g_stage` 状态机」（`{{ global.chat_id }}` 已证明全局变量按会话持久，此路可靠）。
> 2. **全局变量容量**：`g_outline` 约 3–8 KB。全局变量配置未见显式上限，塞一段 8 KB 文本存取一遍确认；超限则 json 用紧凑格式或只存 json 块、预览另发。
> 3. **单选框选项录入位置**（用户输入里 组件类型=单选框 后的选项编辑面板）与 **download-link-publisher 入参**（路径还是文件对象）——都在配置时顺手确认，风险低。

## 六、验证

```bash
cd skills/create-ppt
# 通用模板端到端（本轮新增，重点）
uv run scripts/make_outline.py --template 通用模板 --pages 16 \
  --title "智慧园区建设方案" --sections "背景与目标,现状分析,实施方案,计划安排,总结展望" > outline.json
uv run scripts/make_outline.py --preview outline.json              # 门② 分页卡片
uv run scripts/build_pptx.py --outline outline.json --out 智慧园区建设方案.pptx
uv run scripts/check_pptx.py --outline outline.json 智慧园区建设方案.pptx
uv run scripts/inspect_template.py 智慧园区建设方案.pptx           # 页数/版式核对
```

逐项检查：页数与大纲一致；封面/目录/章节/要点页设计与源模板一致（图片、装饰形状在位）；装饰文本未误改；正文无溢出；产物在 WPS/PowerPoint 可二次编辑。5 套模板各跑一遍，direct 路径用一段文稿跑通一次。

`uv run` 一律不加 timeout 参数（沙箱后端不支持）。
