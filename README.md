# weread-readdata-for-tencent-doc

微信读书书架/已读完列表同步到腾讯文档智能表格。

## Skill 介绍

读取微信读书书架与已读完列表，合并成“书籍列表”，可直接打印为 Markdown 表格，也可按 `bookId` upsert 到腾讯文档智能表格。

适用场景：读取微信读书书架、已读完列表，或同步书籍列表到腾讯文档智能表格。

仓库内容按 Trae / Claude Code skill 目录组织，核心入口是 [`SKILL.md`](./SKILL.md)，脚本与资源放在 `scripts/`、`references/`、`assets/` 等目录中。

## 依赖

使用前请确保已准备：

- `python3`
- `weread-skill`
- 腾讯文档 MCP / mcporter
- `WEREAD_API_KEY`
- `TENCENT_DOCS_TOKEN`

更完整的依赖、鉴权和执行约束请阅读 [`SKILL.md`](./SKILL.md)。

## 安装指引

下面提供两种安装方式：

### 方式一：Shell 明确下载并安装到 `.agent` 目录

适合希望自己控制安装路径和安装过程的用户。下面命令会把 skill 安装到 `${HOME}/.agent/skills/weread-readdata-for-tencent-doc`：

```bash
SKILL_NAME="weread-readdata-for-tencent-doc"
DOWNLOAD_URL="https://github.com/TianLanhe/weread-readdata-for-tencent-doc/archive/refs/heads/main.tar.gz"
INSTALL_ROOT="${HOME}/.agent/skills"
TMP_DIR="$(mktemp -d)"
ARCHIVE_PATH="${TMP_DIR}/${SKILL_NAME}.tar.gz"

# 1. 创建安装目录
mkdir -p "${INSTALL_ROOT}"

# 2. 下载 skill 压缩包
curl -L "${DOWNLOAD_URL}" -o "${ARCHIVE_PATH}"

# 3. 解压到临时目录
tar -xzf "${ARCHIVE_PATH}" -C "${TMP_DIR}"

# 4. 安装到 ~/.agent/skills/<skill-name>
rm -rf "${INSTALL_ROOT}/${SKILL_NAME}"
mv "${TMP_DIR}/${SKILL_NAME}-main" "${INSTALL_ROOT}/${SKILL_NAME}"

# 5. 简单校验并清理临时文件
test -f "${INSTALL_ROOT}/${SKILL_NAME}/SKILL.md"
rm -rf "${TMP_DIR}"
```

安装完成后，可让支持 skills 的 agent / CLI 重新加载 skills，或开启新会话使用 `weread-readdata-for-tencent-doc`。

### 方式二：把下载地址交给 agent 自动安装

下载地址：

```text
https://github.com/TianLanhe/weread-readdata-for-tencent-doc/archive/refs/heads/main.tar.gz
```

可以把下面这段提示词直接交给你的 agent，让 agent 根据当前运行环境判断下载方式、安装方式以及合适的安装位置：

```text
请帮我安装这个 skill：weread-readdata-for-tencent-doc

下载地址：https://github.com/TianLanhe/weread-readdata-for-tencent-doc/archive/refs/heads/main.tar.gz

请根据你所在的 Agent/CLI 环境，判断应该如何下载、解压和安装这个 skill，并选择合适的 skill 安装位置。如果当前环境没有特殊约定，请优先考虑安装到用户级 agent skill 目录。安装完成后，请确认 SKILL.md 存在，并告诉我最终安装路径。
```

## 使用方式

安装后，用自然语言向 agent 描述你的目标即可，例如：

- “帮我读取微信读书书架、已读完列表，或同步书籍列表到腾讯文档智能表格。”
- “先只读取并打印结果，不要写入文档”
- “同步到我提供的目标表，并做 dry-run 预览”

具体命令参数、目标表结构和写入规则见 [`SKILL.md`](./SKILL.md)。

## 安全与权限说明

- 本仓库不包含任何个人 token 或密钥。
- `WEREAD_API_KEY`、`TENCENT_DOCS_TOKEN` 等凭据需要在用户本机自行配置。
- 涉及写入腾讯文档或飞书 Base 的操作，请先确认目标表和权限，建议先使用 dry-run / 只读模式验证。

## License

MIT，详见 [LICENSE](./LICENSE)。
