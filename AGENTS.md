# movk-skills

Claude Code 技能仓库。每个技能是 `skills/<name>/` 下的一个目录，同时要能被外部智能体平台读取配置。

## 目录约定

```text
skills/<skill-name>/
├── SKILL.md            必需，入口与 frontmatter 配置
├── scripts/            可执行脚本，确定性工作交给它们
├── references/         按需加载的长文档（模板、schema、写作规范）
├── templates/          可复用的数据模板
├── assets/             静态资源
└── examples/           输入输出样例
```

`SKILL.md` 是入口，正文写给模型看，控制在 100 行内。模板、schema、格式规范这类只在某一步用得上的长内容放进 `references/`，在正文对应步骤里用相对链接指向它，让模型需要时再读——不要一直占着上下文。

## SKILL.md frontmatter

顶层只允许这几个键：`name`、`description`、`metadata`、`argument-hint`、`compatibility`、`context`、`disable-model-invocation`、`license`、`user-invocable`。其余一律放进 `metadata`，否则 IDE 会报「不支持的属性」——`title`、`opening`、`role`、`prompt`、`run_as` 都不是顶层合法键。

```yaml
---
name: policy-interpretation
description: 面向……当用户提到 A、B、C 时触发。
metadata:
  title: 政策法规解读助手
  opening: |
    您好，我是……
    请告诉我：① … ② … ③ …
    - 示例问句一
    - 示例问句二
  role: ""
  prompt: |
    你是……智能体，覆盖……

    【流程】
    1. …
    【规范】…
---
```

### 字段写法

**新增技能后必须补齐 `description` 与 `metadata` 下的 title、opening、role、prompt 五项。**

- `description` — 一句话讲清覆盖场景、核心能力、产出物，结尾列触发关键词。这是 Claude Code 判断是否加载技能的唯一依据，关键词写全，宁多勿少。
- `metadata.title` — 面向用户的中文助手名，如「政策法规解读助手」。
- `metadata.opening` — 开场白。首行自我介绍，次行用 ①②③ 列出需要用户提供的要素，末尾用 `-` 列出 2-3 条预置示例问句供用户点击。
- `metadata.role` — 系统角色（人设与语气）。无特殊人设要求时置为空字符串 `""`，不要与 prompt 重复。
- `metadata.prompt` — 完整任务指令，可脱离 SKILL.md 正文独立投喂给模型。结构固定为一段身份声明 + `【流程】`编号步骤 + `【规范】`约束条款。流程步骤里写明要调用的脚本命令；规范里写明不得编造哪些内容、缺失字段如何标注。

## 脚本约定

- 用 `uv run skills/<技能名>/scripts/xxx.py` 调用，PEP 723 内联声明依赖，不写 `requirements.txt` 之外的安装步骤。
- **沙箱 cwd 不是技能根目录**（实测为随机运行目录 `/tmp/*-skill-runtime-*`，技能解压在 `<cwd>/skills/<技能名>/` 下）：SKILL.md 里的脚本命令一律带 `skills/<技能名>/` 前缀，跨技能调用同理（如 `skills/web-fetch/scripts/fetch.py`，不用 `../web-fetch/`）；输出文件写在当前工作目录。运行约定里附 find 兜底定位（`find / -name <标志脚本>.py -not -path '*__pycache__*' 2>/dev/null | head -1`）。
- **不要给 `uv run` 加 timeout 参数**，沙箱后端不支持 per-command timeout override，加了必定报错。
- 脚本负责确定性工作（抓取、渲染、格式转换），模型负责判断与写作。边界要清晰。
- 日志走 stderr，结果走 stdout，便于管道消费。
- 抓取外部内容时始终校验 TLS 证书，不要为了绕过证书错误而关闭校验。

## 打包分发

`scripts/pack-skill.sh <skill-name>` 把单个技能打成 `dist/<skill-name>.zip`，zip 内顶层目录即技能名，解压后可直接放进 `skills/`。缓存与系统文件（`__pycache__/`、`*.pyc`、`.DS_Store`）不会进包。

## 文档风格

- 代码、注释、文档一律不用 emoji。
- 中文用全角标点，中英文之间加空格，代码标识符与数字用半角。
- 代码块必须标注语言标识符。
- 同一事实只写一处。部委列表、模板清单这类数据的唯一来源是脚本或 `references/`，SKILL.md 正文引用而不复述。
