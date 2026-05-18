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

可以直接用 `curl` 下载并安装到本机 skill 目录：

```bash
SKILL_NAME="weread-readdata-for-tencent-doc"
INSTALL_DIR="${HOME}/.trae/skills"
TMP_DIR="$(mktemp -d)"

mkdir -p "${INSTALL_DIR}"
curl -L "https://github.com/TianLanhe/weread-readdata-for-tencent-doc/archive/refs/heads/main.tar.gz" | tar -xz -C "${TMP_DIR}"
rm -rf "${INSTALL_DIR}/${SKILL_NAME}"
mv "${TMP_DIR}/${SKILL_NAME}-main" "${INSTALL_DIR}/${SKILL_NAME}"
rm -rf "${TMP_DIR}"
```


安装完成后，可让支持 skills 的 agent / CLI 重新加载 skills，或开启新会话使用 `weread-readdata-for-tencent-doc`。

你也可以把上面的安装指令直接交给 agents，让 agents 帮你自动下载并安装这个 skill。

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
