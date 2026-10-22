# 短信 Skill 1.4.6 同步说明

## 同步来源

以 `agent` 仓库的 **`feat/sms-traffic-driving`** 分支为来源，按 AgentKit 渠道重新打包，
同步到本仓库的 [`skills/byted-sms-sender/`](../skills/byted-sms-sender/SKILL.md)。

| 项目 | 本次取值 |
| --- | --- |
| 同步日期 | 2026-10-22 |
| 源分支 | `feat/sms-traffic-driving` |
| 源提交 | `aa6fb3711a72d06c6e01e1fc2ae5415cdafa92b3` |
| 源工作树 | `/Users/shayne/.codex/worktrees/sms-traffic-validation/agent`，与 `/Users/shayne/workspace/ai/agent` 属于同一 Git 仓库 |
| 源目录 | `skills/specs/skills/byted-sms-sender/` |
| 目标分支 | `feat/sms-skill-source-fields` |
| 本轮对比基线 | `a7cdda738b473cc6a0fefad717f140fca214c682` |
| 版本 | 1.4.6 |
| 渠道 / 环境 | `skills/profiles/public/agentkit.yaml` / `prod` |
| 发布文件 | 66 个；本轮修改 6 个，无新增或删除 |

打包使用 `--require-clean`，构建输入均已提交。本轮保留源工作树中
`.agent/specs/ops/sms_request_source.md` 的既有未提交改动，该文件不属于发布包或构建输入。
只同步生成包内的 Skill 文件，打包脚本、测试、ZIP 和构建记录保留在包外。

## 本轮改动

来源上报收敛为三个请求头：

| 请求头 | 取值 |
| --- | --- |
| `X-Request-Source` | 固定为 `skill` |
| `X-Skill-Version` | 从包内版本文件读取，本次为 `1.4.6` |
| `X-Platform` | Agent 自报的运行产品名；不明确时为 `unknown` |

- CLI 在子命令前传 `--platform <产品名>`；MCP 创建表单及执行 read/write 工具时传顶层
  `platform`，入口取出后写入操作上下文，不进入业务参数、预填数据、确认摘要或幂等键。
- 平台名按声明原值交接，中文在 HTTP 请求头中按 UTF-8 字节发送，CLI 接收原始字符串。
- 原 Skill 名称、渠道、执行方式、操作编号及 MCP 宿主信息不再作为来源请求头发送。
  本机操作编号仍用于日志和流程关联；后台表单保留创建时的上下文。
- `scripts/skill_metadata.json` 仅冻结版本；包版本缺失或非法时明确省略版本并保留本机诊断。

修改文件为 `SKILL.md`、`scripts/api_client.py`、`scripts/qualification_mcp_server.py`、
`scripts/request_context.py`、`scripts/skill_metadata.json` 和 `scripts/sms_cli.py`。
来源声明仅用于统计，不参与账号鉴权、凭据作用域或业务确认。

## 构建与核验

在源工作树根目录，使用 `skills/requirements-dev.txt` 锁定的依赖执行：

```bash
python -E -B skills/scripts/package_skill.py \
  --skill byted-sms-sender --env prod \
  --profile skills/profiles/public/agentkit.yaml \
  --output <尚不存在的输出目录> --require-clean
```

构建包：`byted-sms-sender-1.4.6-prod.zip`。

SHA-256：`1def0a9a443ad893a619f4a8a53ef293316c13682c4a04451fdded7973ee41b2`。

本轮使用该源分支的最新测试脚本，验证同步后的代码及打包入口：

- Python 行为测试 **310/310 通过**，无失败、错误或跳过。
- 另 1 项按原测试前提显式模拟缺失来源元数据并通过；正式包版本文件和三个来源请求头另行核验。
- 版本打包、CLI 及 stdio MCP 启动入口测试 **3/3 通过**，使用临时测试包和模拟 CLI。
- 覆盖中文平台名的 HTTP 实际字节、HTTP 与 CLI 字段一致性、MCP 顶层参数隔离、表单并发与重试，
  以及业务参数、凭据作用域和写入确认保持原有约束。
- 20 个 Python 脚本通过 Python 3.10 语法检查；CLI `--help`、
  `--platform Codex runtime-info`、`quick_validate.py` 和 `git diff --check` 通过。
- 66 个发布文件的哈希及权限与构建记录一致。页面资源本轮无变化，验证聚焦来源脚本与版本打包。

按 `codex-code-review` 复核本轮差异，未发现新增阻断问题。改动仅涉及短信 Skill 和同步说明，
未增加通用平台业务逻辑或额外抽象。

## 当前状态

原 MR !564 已合并；本轮从最新 `main` 新建 `feat/sms-skill-source-fields` 承接增量。
提交、推送及检查状态以 Git 和本轮 MR 的实际记录为准。源工作树及其既有未提交说明文件改动保持原样。
以上为本地离线验证，未验证线上网关透传或指标归类，也未执行真实短信业务操作。
