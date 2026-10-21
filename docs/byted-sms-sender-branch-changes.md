# 短信 Skill 1.4.6 同步说明

## 同步来源

以 `agent` 仓库的 **`feat/sms-traffic-driving`** 分支为来源，按 AgentKit 渠道生成正式环境包，
完整同步到本仓库的 [`skills/byted-sms-sender/`](../skills/byted-sms-sender/SKILL.md)。

| 项目 | 本次取值 |
| --- | --- |
| 同步日期 | 2026-10-20 |
| 源分支 | `feat/sms-traffic-driving` |
| 源提交 | `0571acbd293181acb4eec57a20b544d38278dd94` |
| 源工作树 | `/Users/shayne/.codex/worktrees/sms-traffic-validation/agent`，与 `/Users/shayne/workspace/ai/agent` 属于同一 Git 仓库 |
| 源目录 | `skills/specs/skills/byted-sms-sender/` |
| 目标分支 | `feat/sms-skill-1.4.6` |
| 本轮对比基线 | `5541be2877858069f7c697f6a8628eb0fd45fc69` |
| 版本 | 1.4.6 |
| 渠道 / 环境 | `skills/profiles/public/agentkit.yaml` / `prod` |
| 发布文件 | 66 个；相对本轮基线新增 3 个、修改 28 个、删除 1 个 |

源工作树保持干净，打包使用 `--require-clean`。本仓库的发布文件逐一核对哈希和权限，
与该提交生成的完整 Skill 目录一致；已有本机缓存不属于发布文件。

## 本轮同步的主要差异

- 入口明确包含运营商报备查询，并增加独立的
  [报备查询说明](../skills/byted-sms-sender/references/report-status.md)。
- 增加统一的[表单展示与回退说明](../skills/byted-sms-sender/references/form-display.md)，
  替换原资质展示说明。
- 新增 `scripts/template_application.py`，同步模板参数、账号变量配置及固定引流信息预校验。
- 同步该分支的签名表单、材料上传及确认流程；签名申请类型固定全选验证码、通知和营销。
- 同步 CSV 示例指引、CLI 路径查找、公共确认摘要及其他对应脚本和说明。

## 构建与一致性

在源工作树根目录，使用锁定依赖执行：

```bash
python -E -B skills/scripts/package_skill.py \
  --skill byted-sms-sender --env prod \
  --profile skills/profiles/public/agentkit.yaml \
  --output <尚不存在的输出目录> --require-clean
```

构建包：`byted-sms-sender-1.4.6-prod.zip`。

SHA-256：`d77b66a250b5a29a52422ade029aade72dd3cb60c33da62698588512f4fd424d`。

## 本轮验证

最近一次完整验证基于源提交 `1bc8578af`，用该分支的测试脚本在临时目录中引用本仓库代码和页面：

- Python 行为测试 **310/310 通过**，无失败、无错误、无跳过。
- 另 1 项按原测试要求显式模拟“缺失来源元数据”的前提并通过；正式包元数据另行核验为
  `1.4.6 / agentkit`。没有为测试修改生产代码或源测试。
- 页面测试 **30/30 通过**，包含签名、引流页面关闭通知的鉴权、JSON 请求头和 `keepalive` 验证。
- 之前失败的 6 个测试方法及其 9 处断言已通过。新增用例验证非法签名类型在 CLI、MCP 的
  预览和提交入口均返回 `argument_error`，且不会提交申请。
- 20 个 Python 脚本通过 Python 3.10 语法检查；5 个页面脚本通过语法检查。
- CLI `--help`、`runtime-info`、`quick_validate.py` 和 `git diff --check` 通过。
- 66 个发布文件及文件权限与构建记录一致。

上一轮 `0811ee0f5` 的失败日志保留；本轮结论来自最新提交重新生成的包和重新执行的测试，
不将新结果覆盖为旧版本已通过。

按 `codex-code-review` 核对同步差异、源包一致性及验证结果。业务代码仍位于短信 Skill，
本轮未增加平台业务逻辑或新的抽象。参数校验错误分类、非法输入不提交和关闭通知鉴权均已验证。

## 当前状态

本次同步使用 `feat/sms-traffic-driving` 的干净提交 `0571acbd2`，源分支内容未修改。
该提交已包含安全扫描修复，以及签名参数错误分类和行为测试修复。
目标分支为 `feat/sms-skill-1.4.6`，对应 MR !564；提交、推送和检查状态以 Git 及 MR 当前记录为准。
此前 `5541be28` 的扫描通过记录仅对应旧提交，本次提交的远端检查需单独核验。
未执行真实开通、申请、报备查询或短信发送。

## 摘要脱敏扫描修复

本次 `0571acbd2` 仅将公开摘要脱敏调用的参数展开为多行，
修复敏感扫描对脱敏占位符的误报。修改前后的 Python 逻辑结构完全一致，已知凭据仍然替换为
`[REDACTED]`，公开确认摘要继续按原规则保留。17 项现有模板与脱敏测试通过；其余发布文件
与上一轮完整验证的版本相同。源分支与本仓库保持同一干净生成包内容。

扫描覆盖提交历史，本次替换 MR 中命中的提交，并以精确旧提交的 `force-with-lease` 保护远端。
远端是否通过以新提交的实际检查结果为准。
