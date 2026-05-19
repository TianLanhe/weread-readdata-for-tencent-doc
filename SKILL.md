---
name: weread-readdata-for-tencent-doc
description: "Read WeRead/微信读书 bookshelf data through the installed `weread-skill` Agent API Gateway (not native/raw WeRead APIs), print the consolidated book list, or sync it into the `书籍列表` table of a Tencent Docs SmartSheet/腾讯文档智能表格. Trigger only when the request is specifically about 同步/导入/整理微信读书书架、书籍列表到腾讯智能表格；不要用于微信读书阅读时长同步、其他平台多维表格、腾讯普通表格/Excel、腾讯文档正文、知识库，或非微信读书书籍数据同步。"
metadata:
  requires:
    bins: ["python3", "mcporter"]
    env: ["WEREAD_API_KEY", "TENCENT_DOCS_TOKEN"]
---

# 微信读书书架 → 腾讯文档智能表格 / 表格输出

这个 skill 负责两件事：

1. **只读打印**：读取微信读书书架数据，整理为一份“书籍列表”数据。
2. **写入腾讯智能表格**：把同一份数据按 `bookId` upsert 到腾讯文档智能表格的 `书籍列表` 工作表。

## 依赖与配置

执行这个 skill 时，优先按下面的依赖关系工作：

### 依赖 skill

- `weread-skill`
  - 用来读取微信读书数据、理解 WeRead 的接口能力与已有口径，尤其是 `/shelf/sync` 的书架结构、`/book/info` 的书籍详情、`/book/getprogress` 的阅读进度，以及 `/book/chapterinfo` 的章节可读/购买信息。
  - 所有微信读书数据读取都必须走 `weread-skill` 提供的 Agent API Gateway（`https://i.weread.qq.com/api/agent/gateway`，通过 `api_name` 指定能力），不要直接调用微信读书原生 / raw API 或自行抓取微信读书 App/Web 接口。
- `tencent-docs`
  - 用来读写腾讯文档智能表格、校验字段、复制模板智能表格。

如果遇到腾讯文档认证或权限报错，先按 `tencent-docs` 的 `references/auth.md` 流程处理，不要盲目重试写入。

### 必要环境

- 环境变量：`WEREAD_API_KEY`
- 腾讯文档鉴权：`TENCENT_DOCS_TOKEN`
- 可执行命令：`python3`、`mcporter`

如果 `WEREAD_API_KEY` 缺失，直接告诉用户先配置，不要继续执行。
如果腾讯文档授权缺失或过期，按 `tencent-docs` skill 的授权流程重新配置。

### 依赖安装

这个 skill 依赖三个外部组件：

1. `python3`
2. `weread-skill`
3. 腾讯文档 MCP / `mcporter`

推荐的人工安装方式：

#### 安装 Python 3

当执行 skill 前发现 `python3` 命令不存在时，允许先在终端自动安装，再继续执行；**这属于环境准备，不要把安装逻辑写进 `scripts/sync_weread_readdata_to_tencent_doc.py`。**

优先按当前机器环境选择：

**macOS（推荐，已安装 Homebrew）**

```bash
brew install python@3.12
python3 --version
```

**macOS（未安装 Homebrew 时先安装 Homebrew，再安装 Python 3）**

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.12
python3 --version
```

**Debian / Ubuntu**

```bash
sudo apt-get update
sudo apt-get install -y python3
python3 --version
```

如果用户环境里缺少 `python3`，应先执行上述环境安装流程，再继续使用本 skill；不要因为缺少 `python3` 直接放弃。

#### 安装微信读书 skill

1. 从 "https://cdn.weread.qq.com/skills/weread-skills.zip” 下载并安装 skill，重命名 skill 为 weread-skill
2. 提示用户下载最新版本移动端“微信读书App”。在“我->设置->微信读书Skill->获取API Key”中获取API Key并配置 WEREAD_API_KEY 环境变量

#### 安装 / 配置腾讯文档 MCP

1. 从 "https://cdn.addon.tencentsuite.com/static/tencent-docs.zip" 下载 zip 包并 unzip 解压，安装这个 skills
2. 提示用户 “https://docs.qq.com/scenario/open-claw.html?nlc=1” 从这个网址获取并配置 TENCENT_DOCS_TOKEN 环境变量

```bash
mcporter list tencent-docs
```

## 何时触发

下列请求都应该触发这个 skill：

- “把我的微信读书书架同步到腾讯智能表格”
- “同步微信读书书架到腾讯文档的书籍列表”
- “整理微信读书书架书单，写入书籍列表数据表”
- “导入 weread 书架数据到腾讯 SmartSheet，按 bookId 去重更新”
- “我只想看微信读书书架书单，不需要导入腾讯文档”

下列请求**不要**触发这个 skill：

- 微信读书每日阅读时长、阅读统计同步（应使用阅读时长 skill）
- 把微信读书数据同步到其他平台的多维表格
- 把微信读书数据同步到腾讯普通表格（sheet / Excel）、腾讯文档正文、知识库等非智能表格产品
- 把其他来源的数据同步到腾讯文档智能表格
- 与微信读书书架 / 书籍列表无关的文档整理、报表搬运、表格写入需求

## 模式判定

收到请求后，先判断用户要的是哪一种模式：

### A. print-only / 只读模式

如果用户明确表达以下意思，就走**只读打印**，不要写腾讯智能表格：

- “只需要读取”
- “不用导入腾讯文档”
- “先打印出来”
- “只看表格结果”

此时直接运行脚本的 `--print-only` 模式，然后把结果以 Markdown 表格返回给用户。

### B. 写入已有腾讯智能表格

如果用户给了下面任一信息，就按已有腾讯智能表格写入：

- 完整腾讯文档智能表格链接（最好能识别 `file_id`，并带 `sheet_id` / `sheet` / `tab` 参数）
- `file_id + sheet_id`

写入前先校验目标工作表结构；若缺字段，先告诉用户修正表结构，不要盲写。

如果用户给的 `sheet_id` **格式不合法或缺失**，不要直接失败：

1. 先检索整个腾讯智能表格文件的所有工作表；
2. 找出字段满足“书籍列表”字段要求的工作表；
3. 优先使用名为 `书籍列表` 的工作表；否则使用第一个符合要求的工作表；
4. 回复用户时明确说明发生了自动回退与最终命中的 `sheet_id`。

### C. 未给腾讯智能表格，询问是否新建

如果用户要写入腾讯文档智能表格，但**没有提供智能表格链接 / file_id + sheet_id**，必须先追问：

> 你要不要我直接新建一个保存微信读书书架书单的腾讯文档智能表格？

确认后，再复制固定模板创建腾讯智能表格副本。**不要在用户未确认时直接复制模板。**

## 复制腾讯智能表格模板规则

当用户确认要新建腾讯智能表格时，不要从空白表格创建字段，也不要在代码里初始化工作表结构；直接从下面的腾讯文档智能表格模板复制副本：

- 模板链接：`https://docs.qq.com/smartsheet/DYXpmanNXaURNWVB4?nlc=1&no_promotion=1&is_blank_or_template=template&tab=sc_tNPtzz`
- 模板 `file_id`：`DYXpmanNXaURNWVB4`

说明：

- 主脚本在 `--init-smartsheet` 路径下调用 `tencent-docs` 的 `manage.copy_file` 复制该模板。
- 复制完成后，自动在副本中查找满足字段要求的工作表，优先使用名为 `书籍列表` 的工作表。
- 模板本身负责提供字段、视图、格式和其他初始化内容；脚本只做复制、定位目标工作表、校验字段和写入数据。
- 如果要修改新建智能表格的结构、视图或样式，应该修改模板文档本身，而不是修改脚本里的字段初始化逻辑。

## 数据来源与口径

所有微信读书数据都通过 `weread-skill` 的 Agent API Gateway 获取，不直接请求微信读书原生接口。字段推导、回退策略、可读性判断、进度换算与写入细节以脚本实现为准；SKILL.md 只保留使用方式、触发条件和目标表要求。

## 目标表结构要求

写入的目标工作表必须包含以下字段（字段类型允许由模板决定，脚本会按字段类型生成对应的腾讯 SmartSheet FieldValue）：

- `bookId`
- `书名`
- `书架分类`
- `价格`
- `作者`
- `分类`
- `一级分类`
- `是否可读`
- `评分`
- `推荐值`
- `阅读时长（秒）`
- `阅读时长（时）`
- `阅读时长（分）`
- `阅读时长格式化`
- `封面`
- `字数（单位：万字）`
- `简介`
- `阅读进度`
- `是否已读完`
- `阅读完成时间`
- `已读完年`
- `已读完年月`

在写入已有工作表前，先执行字段存在性校验；字段缺失就停止。腾讯智能表格中的公式 / 计算类字段仍不应写入，例如模板中的 `每小时阅读字数（万）`、`每小时阅读字数（统计用）`、`字数（统计用）`；`阅读顺序` 当前也不由脚本写入，除非后续明确排序口径。其余字段的具体取值与写入格式以脚本实现为准。

## 推荐执行命令

### 1) print-only：只读取并打印

```bash
python3 ${HOME}/.trae/skills/weread-readdata-for-tencent-doc/scripts/sync_weread_readdata_to_tencent_doc.py \
  --print-only
```

### 2) 写入已有腾讯智能表格（完整链接）

```bash
python3 ${HOME}/.trae/skills/weread-readdata-for-tencent-doc/scripts/sync_weread_readdata_to_tencent_doc.py \
  --table-url "https://docs.qq.com/smartsheet/DRXxxxxxx?sheet_id=sheet_abc123"
```

### 3) 写入已有腾讯智能表格（file_id + sheet_id）

```bash
python3 ${HOME}/.trae/skills/weread-readdata-for-tencent-doc/scripts/sync_weread_readdata_to_tencent_doc.py \
  --file-id DRXxxxxxx \
  --sheet-id sheet_abc123
```

### 4) 用户确认后，从模板复制腾讯智能表格并写入

```bash
python3 ${HOME}/.trae/skills/weread-readdata-for-tencent-doc/scripts/sync_weread_readdata_to_tencent_doc.py \
  --init-smartsheet \
  --file-name "微信读书书架书单"
```

### 5) 完全对齐：删除腾讯表中已不在微信读书合并列表里的旧记录

```bash
python3 ${HOME}/.trae/skills/weread-readdata-for-tencent-doc/scripts/sync_weread_readdata_to_tencent_doc.py \
  --table-url "https://docs.qq.com/smartsheet/DRXxxxxxx?sheet_id=sheet_abc123" \
  --delete-missing
```

## 常用参数

```bash
--print-only                  # 只读取打印，不做任何腾讯智能表格操作
--table-url <url>             # 腾讯文档智能表格链接，建议带 sheet_id/sheet/tab 参数
--file-id <id>                # 腾讯文档智能表格 file_id
--sheet-id <id>               # 工作表 sheet_id
--init-smartsheet             # 从固定模板复制腾讯智能表格副本，并把数据写入其“书籍列表”工作表
--file-name <name>            # 复制后的腾讯智能表格名字，默认“微信读书书架书单”
--folder-id <id>              # 可选，把副本放到指定文件夹
--dry-run                     # 只计算 upsert / delete 结果，不实际写入已有腾讯智能表格
--delete-missing              # 删除目标表中 bookId 已不在微信读书合并列表里的旧记录；需要用户明确要求或确认
--max-workers <n>             # 批量获取书籍详情的并发数，默认 10
```

## 执行流程

### 1. 判断模式

- 只读 -> `--print-only`
- 已给腾讯智能表格 -> 直接校验并写入
- 要写入但没给腾讯智能表格 -> 先问是否从固定模板复制腾讯智能表格

### 2. 返回结果

返回时至少说明：

- `总书籍数`
- print-only 还是 sync
- 若写入：`新增 / 更新 / 跳过 / 删除` 数量
- 若发生了 sheet_id 自动回退：返回原始 `sheet_id` 与最终命中的 `sheet_id`
- 若复制了模板腾讯智能表格：返回 `file_id`、`sheet_id`、文件名称、可访问链接（若 MCP 返回）、模板来源链接
- 若有封面图片字段无法写入等非致命问题，展示 `warnings`

## 返回格式要求

### print-only 模式

优先返回脚本输出里的 `markdown_table`，直接展示成 Markdown 表格，不需要再写一遍 JSON。

### 写入模式

先给简要摘要，再按需附上表格：

- 总书籍数
- 新增记录数
- 更新记录数
- 跳过记录数
- 删除记录数
- 目标腾讯智能表格 / 工作表
- 如果发生了 `sheet_id` 自动回退，明确说明是因为用户提供的 `sheet_id` 格式不合法或缺失
- 如果是 `dry-run`，明确说明未实际写入

## 注意事项

- 这是一个**可能包含写操作**的 skill；复制模板创建腾讯智能表格、写入腾讯智能表格、删除旧记录前都要先得到用户确认。
- 用户只说“读取 / 打印 / 看一下”时，不要顺手写入腾讯文档。
- `阅读时长（秒）` 单位始终是秒，不要误当分钟。
- 书籍唯一键始终是 `bookId`，不要用书名做 upsert 主键。
- `--delete-missing` 有删除风险，只有用户明确说“完全同步 / 删除不在微信读书里的旧记录 / 与微信读书保持一致”时才使用。
- 当用户误把其他 token 当成 `sheet_id` 传入，或链接里没有 `sheet_id` 时，要自动扫描整个腾讯智能表格找到真正可写的 `书籍列表` 工作表，而不是直接报错结束。
- `scripts/sync_weread_readdata_to_tencent_doc.py` 只允许包含两类逻辑：
  1. 通过 `weread-skill` 能力读取微信读书书架、阅读进度、书籍详情，并进行字段合并
  2. 腾讯文档智能表格写入 / 从固定模板复制新智能表格与其直接相关的最小逻辑
- 不要把 skill 安装、MCP 安装、依赖检测、环境探测、升级向导等非核心流程写进这个 Python 脚本。
- 后续如果要更新安装方式、依赖说明、触发条件、操作约束，优先修改 `SKILL.md`、`references/`、`assets/`，**不要因为这些非书籍数据获取 / 腾讯智能表格写入需求去改 Python 代码。**