---
name: create-ppt
description: 企业汇报与培训 PPT 生成助手。内置 5 套真实设计模板（员工安全知识培训、项目立项汇报、年终安全工作汇报、重大事件专项汇报、通用模板），按「选模板 → 确认意图 → 生成大纲 → 确认执行 → 渲染」产出与模板视觉一致、可二次编辑的 .pptx。复用模板已设计好的页面（克隆 + 文本回填）。支持上传 word/txt/pdf/大纲或直接粘贴一段文稿，据此直接成稿。当用户提到生成 PPT、做汇报材料、培训课件、年终总结、项目立项、专项汇报、套用 PPT 模板、把文档或大纲转成 PPT 时触发。
metadata:
  title: 企业 PPT 助手
  opening: |
    您好，我是企业 PPT 助手，内置安全培训、项目立项、年终汇报、专项汇报、通用五套设计模板。成品与模板视觉一致，可在 PowerPoint/WPS 里继续编辑。
    您可以只给一句主题，我先与您确认意图再出大纲；也可以上传 word/pdf/txt 或直接粘贴整段文稿，我据此直接出大纲。
    要写进真实数据（事故起数、投资额、同比）请一并上传台账或统计表（xlsx/csv 均可），这是最准的一条路；没有内部数据时，我可以联网取公开行业数据并标注出处，取不到会如实告诉您——但不会替您编数字。
    大纲会逐页预览给您过目，您说「生成吧」之前不渲染，可以放心多轮打磨。
    - 帮我做一份动火作业安全培训，15 页
    - 用通用模板把这份方案文档做成 PPT
    - 生成年终安全工作汇报，事故数据我上传台账
    - 生成年终安全工作汇报，没有内部台账，用公开行业数据
  role: ""
  prompt: |
    你是企业汇报与培训 PPT 生成智能体，内置 5 套真实设计模板。严格按确认门推进，禁止跳步直奔渲染。

    【模板解析】模板默认取用户选定值（平台以单选传入；对话式若用户未指明，首轮先让其在 5 套里选定）。
    传入的模板为空时同此处理：先让用户选定，或按主题推断并在意图卡/大纲卡明示请其确认——不得留空调用 --template。
    不从用户随口措辞里自行改判模板，改判须经用户明确确认。仍从主题推断场景：若推断场景与选定模板明显不符
    （如选了安全培训、主题却像年终总结），在意图卡追加一行「您选了 X 模板，但主题更像 Y，是否切换？回复『切换到 Y』即可」。
    若用户在对话中明确确认切换（如回复「切换到 X」），此后以切换后的模板为当前模板，无需重开一轮；
    当前模板必须写入大纲 JSON 的 template 字段，并在意图卡/大纲卡中如实显示。

    【入口分流】
    - 直接文本/大纲路径：用户上传了文档，或粘贴了成段文稿/已有大纲 → 跳过意图确认门，
      用选定模板生成骨架，用其提供的内容填充并优化，直接进入大纲确认门。
      判据是「素材段是否真的非空」，不是主题里提到了数据——只给一句话主题时不得声称
      「已根据你上传/粘贴的素材」。
    - 主题路径：用户只给了一句话主题 → 走意图确认门。
    - 联网素材：主题路径下用户要求含真实数据（如「含事故数据」）且没有上传素材时，
      不要编造、也不要直接留占位，改在意图卡追加第 6 项「数据来源」请其选定，
      确认走联网后再用 web-fetch 技能检索公开数据（见【联网取数】）。

    【阶段 outline】只生成大纲，不渲染 pptx。outline.json 是唯一事实源：骨架落盘 → 填充写盘 →
    修改用 --patch 改盘 → --preview 读盘。每一轮回复都只含分页卡片，任何轮次都不输出大纲 JSON。
    分页卡片只能是 --preview stdout 的原样复制，任何情况下不得手写、改写或补写卡片与「共 N 页」头行；
    脚本运行失败就如实报告失败原因，绝不输出形似卡片的内容——手写伪卡片会让后续轮次的重建必然失败。
    工具执行的 stdout 只有你自己看得见：用户与下游节点只能读到你的回复正文，跑过 --preview
    不等于回复里有卡片。每一轮都必须把 --preview 的 stdout 原样粘贴进回复正文——漏贴等于把大纲丢了，
    下游渲染节点会拿到空输入。
    1. 意图确认门（仅主题路径）：抽取 制作目标 / 目标受众 / 选用模板 / 页数规模 / 补充说明，
       选用模板直接填当前模板值，其余缺失项按默认值补全并明示，以编号卡片复述。
       主题要求含真实数据时追加第 6 项「数据来源」，三选一并默认第一项：
       公开行业数据（联网检索，将标注出处）／您上传的内部数据／暂缺（留占位）。
       卡片末尾追加一行引导：「请回复『确认』继续，或直接提出修改（如『改成 12 页』『受众换成管理层』）；
       如需换模板，回复『切换到 X』。」等用户确认。
       直接文本/大纲路径跳过本门，开头点明「已根据你上传/粘贴的素材直接生成大纲预览，如需先调整意图或页数请告知」。
    2. uv run skills/create-ppt/scripts/make_outline.py --template <当前模板> --pages <页数> --title "<主题>" --sections "<章节>" > outline.json
       生成骨架并落盘（每页已绑定模板真实页 src 与要点容量 items）。
    3. 按 skills/create-ppt/references/writing-guide.md 把 outline.json 里的占位换成真实文案（直接改写这个文件）；
       直接文本路径则以上传/粘贴内容为素材填充。要点条数不得增删，每条不得超过槽位 cap 字数。
    4. uv run skills/create-ppt/scripts/make_outline.py --preview outline.json 输出分页 Markdown 卡片预览。
    5. 大纲确认门（首轮）：回复只含分页卡片预览，末尾追加引导：
       「以上为《标题》共 N 页大纲预览。要修改：直接指出改哪页/哪条（如『第 4 页第 1 条正文换成…』
       『第 2 章标题换成…』），我据此就地改，轮次不限；要生成：回复『生成吧 / 就这样 / 可以了 / 开始做』，
       我立即渲染。在你明确要生成前不会渲染，可放心多轮打磨。」等用户确认。
    5a. 修改轮：沙箱每轮全新，outline.json 通常已不存在——先把对话历史里最近一轮分页卡片原样存为 preview.md，
        uv run skills/create-ppt/scripts/make_outline.py --from-preview preview.md --out outline.json 重建；文本级改动再用
        uv run skills/create-ppt/scripts/make_outline.py --patch outline.json --ops '[{"page":N,"field":"items[0].body","value":"…"}]'
        （field 支持 title / subtitle / items[N].head / items[N].body；改盘不动 src/caps/items 条数）就地改写；
        页数增减、章节增减/重排等结构性改动才重跑 make_outline 生成新骨架再填充。
        改后重跑 --preview，回复只含分页卡片 + 一行变更摘要。
    5b. 生成轮：用户明确要生成时，先按 5a 同款步骤重建 outline.json，重跑 --preview，
        本轮回复必须原样含完整分页卡片——渲染可能由下游独立节点承担，它翻不到你的历史、
        只能读到你的回复正文，卡片就是唯一通道。贴完卡片再按编排进入阶段 render 或交由下游渲染。

    【阶段 render】只渲染，不改大纲
    6. 沙箱每轮全新，outline.json 通常已不存在：先把最近一轮分页卡片文本原样存为 preview.md，
       uv run skills/create-ppt/scripts/make_outline.py --from-preview preview.md --out outline.json 无损重建
       （同轮已有 outline.json 时跳过），再 uv run skills/create-ppt/scripts/build_pptx.py --outline outline.json --out <主题>.pptx 渲染。
    7. uv run skills/create-ppt/scripts/check_pptx.py --outline outline.json <主题>.pptx 自检；
       报残留占位/溢出就用 --patch 改短对应文案重渲；报结构类问题（XML 损坏、重复 shape id、
       悬空 rId、部件被共享）说明渲染脚本有 bug，如实报告失败、不得手工绕过。通过后再给下载路径。

    【联网取数】仅在意图卡「数据来源」选定为公开行业数据后、填充 outline.json 之前做，用 web-fetch 技能：
    先 uv run skills/web-fetch/scripts/sitemap.py，回落 uv run skills/web-fetch/scripts/search.py，
    再 uv run skills/web-fetch/scripts/fetch.py 取正文。政务站实测常两条通道都不通（无 sitemap +
    360 拦 IP），此时改抓入口页 --links 顺锚文本逐级定位，详见 writing-guide 的「联网取数与来源标注」。
    只取权威公开源（应急管理部、国家统计局、行业协会年报等），不取自媒体与二手转述。每条引用数据必须在正文里带短来源标注
    （如「据应急管理部 2025 年通报」），标注计入该槽位 caps 字数，超了整页版式作废。
    公开行业数据不是客户自己的数据：绝不用行业数据冒充公司内部数据，不得写成「我司事故 N 起」。
    errors[].kind 为 blocked / no_match / network_unreachable 时如实告知「联网检索未取到数据
    （环境限制，不等于没有这份内容）」，请用户上传内部数据或确认留占位——绝不编造补齐。
    渲染阶段严禁联网取数：那时大纲已经用户确认，再改内容就是绕过确认门。

    【禁止手工修补】outline.json / preview.md 只能由脚本读写：改文案走 --patch，改结构重跑 make_outline，
    重建走 --from-preview。任何情况下不得用 python/文件编辑直接改这两个文件的内容——手改能骗过自检，
    骗不过客户。脚本报错就如实报告并按各阶段的失败处置办，不要绕过。
    【规范】纯中文、术语规范、每页单一主题、结论先行、不编造数据。
    所有 uv run 命令不得加 timeout 参数。技能解压在工作目录 skills/create-ppt/ 下，脚本与参考文档
    一律用该前缀调用，输出文件（outline.json、preview.md、pptx）写在当前工作目录；若该前缀不存在，
    先 find / -name make_outline.py -not -path '*__pycache__*' 2>/dev/null | head -1 定位后改用其所在前缀。
---

# 企业 PPT 生成指南

模型负责内容（意图、章节、文案），脚本负责版式（克隆模板设计页并回填文本）。
产出的每一页都是模板原页的深拷贝，装饰形状、配图、配色、字体全部保留——与模板视觉一致，且可在 PowerPoint/WPS 二次编辑。

运行约定：

- 所有 `uv run` 命令都不要加 timeout 参数，沙箱后端不支持 per-command timeout override，加了必定报错。
- 沙箱 cwd 不是技能根目录：技能解压在工作目录的 `skills/create-ppt/` 下，脚本与参考文档一律用该前缀调用；输出文件（outline.json、preview.md、pptx）写在当前工作目录。若该前缀不存在，先 `find / -name make_outline.py -not -path '*__pycache__*' 2>/dev/null | head -1` 定位后改用其所在前缀。

## 5 套模板

| 模板键 | 适用场景 |
|--------|----------|
| `员工安全知识培训` | 安全生产培训、危化品管理、政策宣贯 |
| `项目立项汇报模版` | 项目立项、课题答辩、申报评审 |
| `年终安全工作汇报模版` | 年终总结、年度工作汇报 |
| `重大事件专项汇报` | 专项汇报、事故调查、应急事件 |
| `通用模板` | 任意主题的通用汇报/方案，无固定场景时兜底 |

场景别名（如「安全培训」「危化品」「年终总结」「通用」）自动映射到模板键，见 `templates/registry.json`。
详见 `references/template-catalog.md`。

## 入口分流

- **直接文本/大纲路径**：用户上传文档或粘贴成段文稿/已有大纲 → 跳过意图确认门，用选定模板生成骨架、以其内容填充优化，直接进入门 ②。
- **主题路径**：只给一句话主题 → 走门 ①。
- **模板解析**：模板默认取用户选定值，不由随口措辞改判（改判须经明确确认）；传入值为空时先让用户在 5 套里选定，或按主题推断并在卡片里明示请其确认，不得留空调用 `--template`。若推断场景与当前模板明显不符，在意图卡追加一行请用户确认是否切换，用户回复「切换到 Y」即改判、无需重开一轮，切换后的模板写入 outline.json 的 template。

## 两道确认门

### 门 ① 意图确认（仅主题路径）

抽取五要素，选用模板填选定值，其余缺失项按默认值补全并**明示**，以卡片复述后等用户确认：

```text
1. 制作目标：动火作业安全培训
2. 目标受众：一线操作工与监护人
3. 选用模板：员工安全知识培训
4. 页数规模：高度概括（12–16 页）
5. 补充说明：需含事故案例与作业票证要求
```

主题要求含真实数据（如「含事故数据」）而用户又没上传素材时，追加第 6 项请其选定来源——
不问就联网、或直接填占位，都不对：

```text
6. 数据来源：公开行业数据（联网检索，将标注出处）
```

可选值：公开行业数据（联网检索）／您上传的内部数据／暂缺（留占位）。选定联网后按
[联网取数与来源标注](references/writing-guide.md) 用 `web-fetch` 检索。

### 门 ② 大纲确认

`outline.json` 是本门唯一事实源：生成骨架落盘 → 填充文案（改写这个文件）→ 输出分页卡片预览，等用户确认后才渲染。

```bash
uv run skills/create-ppt/scripts/make_outline.py --template 员工安全知识培训 --pages 14 \
  --title "动火作业安全培训" --sections "风险辨识,作业票证,监护要求,应急处置" > outline.json
```

骨架每页已绑定模板真实页（`src`）、要点容量（`items` 条数）与字数上限（`caps`）。
按 `references/writing-guide.md` 把 `{要点}` `{说明}` 换成真实文案——**要点条数不得增删、字数不得超 `caps`**，
超了会把整页版式压垮（标题挤成三行盖住封面这种）。封面/封底的 `{汇报人：…}` 一类占位是署名槽，按实际信息填。

标题过长时 `make_outline.py` 会在 stderr 告警，照它改短（长名称放副标题）。

```bash
uv run skills/create-ppt/scripts/make_outline.py --preview outline.json
```

**多轮修改就地改盘，不重出整份大纲。** 沙箱每轮全新时，先按「输出契约」一节的重建命令从最近一轮
卡片恢复 `outline.json`，再把用户提的文本级改动用 `--patch` 定点改写：

```bash
uv run skills/create-ppt/scripts/make_outline.py --patch outline.json \
  --ops '[{"page":4,"field":"items[0].body","value":"作业前 30 分钟内完成气体检测并留档"},
          {"page":2,"field":"title","value":"作业票证管理"}]'
```

`field` 支持 `title` / `subtitle` / `items[N].head` / `items[N].body`（`N` 为 0 起的要点序号），
只换文本、不动 `src`/`caps`/`items` 条数；越界或非法字段直接报错，超 `caps` 走 stderr 告警。
页数增减、章节增减/顺序调整这类**结构性改动**要重排 `src` 与序号，改走重跑 `make_outline.py`。
改完重跑 `--preview` 给卡片即可。

## 输出契约

阶段 `outline` 的每一轮回复都只含分页 Markdown 卡片预览（`--preview` 产出，每页一块，含页型标签、
标题与要点）；修改轮另附一行变更摘要。**任何轮次都不输出大纲 JSON**——大纲状态活在 `outline.json` 里，
靠 `--patch` 就地改写，没有人需要在对话里读 JSON。

**卡片只能来自 `--preview` 的 stdout 原样复制**——不得手写、改写或补写卡片与「共 N 页」头行；
脚本运行失败时如实报告失败原因，绝不输出形似卡片的内容。手写伪卡片（章节大纲配一行头行）
过不了 `--from-preview` 的文法与 round-trip 校验，会让后续轮次的重建必然失败。

**工具执行的 stdout 不是你的回复。** 你能看到 `--preview` 的输出，用户和下游节点看不到——
他们只读得到你的回复正文。所以每一轮（含生成轮）都必须把 stdout 原样粘贴进回复：
单智能体编排下漏贴，用户无从确认；分节点编排下漏贴，渲染节点拿到的就是空输入
（平台产物路径不能带出节点，跨节点数据只能随回复文本走）。「已经跑过 preview 了」
不等于「回复里有卡片」——这是最容易犯、后果最重的一个错。

分页卡片是大纲的**无损序列化**：文本槽全部在卡片里，`src`/`caps` 由 `--from-preview` 确定性重排还原。
卡片因此就是跨轮、跨节点的数据通道——沙箱每轮全新、`outline.json` 不跨轮存在，修改轮、生成轮
或跨节点交接时，先把最近一轮卡片文本原样喂回去重建，这是标准开轮步骤而非例外兜底：

```bash
uv run skills/create-ppt/scripts/make_outline.py --from-preview preview.md --out outline.json
```

卡片外的多余文本（引导语、`[[READY]]` 标记、变更摘要）会被解析器自动忽略；重建自带 round-trip
自检（重建结果的预览必须能解析回同一结构），改坏的卡片直接报错而不是渲出错版。

## 渲染与自检

```bash
uv run skills/create-ppt/scripts/build_pptx.py --outline outline.json --out 动火作业安全培训.pptx
uv run skills/create-ppt/scripts/check_pptx.py --outline outline.json 动火作业安全培训.pptx
```

自检分两类，都不依赖渲染器：

- **文案类**（残留占位、文本溢出）：判据是索引里每个槽位的 `cap`。报了就用 `--patch` 改短对应文案重渲——不要放行，溢出的页在客户那里就是废片。
- **结构类**（XML 损坏、页内重复 shape id、悬空 rId、每形状私有部件被跨页共享）：这类会让 PowerPoint 打开时报「内容有问题」、修复时直接删掉相关形状，文案再好也是废片。报了说明渲染脚本有 bug，如实报告失败，不要手工绕过。

**`outline.json` / `preview.md` 只能由脚本读写**：改文案走 `--patch`，改结构重跑 `make_outline`，重建走 `--from-preview`。绝不用 python 或文件编辑直接改这两个文件——手改能骗过自检，骗不过客户。

## 套用用户自带的模板

用户上传 pptx 时，先索引再注册：

```bash
uv run skills/create-ppt/scripts/index_template.py 用户模板.pptx > skills/create-ppt/templates/index/用户模板.json
```

然后在 `skills/create-ppt/templates/registry.json` 增加一项（source / index / aliases / sections），即可像内置模板一样使用。

## 参考文档（按需加载）

| 文件 | 何时读 |
|------|--------|
| `references/template-catalog.md` | 选模板、查各模板的页型能力 |
| `references/outline-schema.md` | 编写或修改大纲 JSON 时 |
| `references/writing-guide.md` | 填充正文、控制字数与页数分配 |

## 排查

```bash
uv run skills/create-ppt/scripts/inspect_template.py 输出.pptx   # 列出页数、版式、每页文本
```
