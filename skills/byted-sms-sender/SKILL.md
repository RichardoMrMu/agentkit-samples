---
name: byted-sms-sender
license: Apache-2.0
metadata:
  author: volcengine-sms-team
  version: 1.4.6
description: 火山引擎国内短信服务（Volcengine SMS）。支持短信服务开通，资质、签名和模板的申请与查询，运营商报备查询，引流链接及号码录入，单条、群发和定时发送，以及发送记录、回执、统计查询与失败排查。
---

# 火山引擎短信

提供国内短信开通，资质、签名和模板申请与查询、运营商报备查询，单条与群发发送，以及发送记录、回执和统计分析。
Agent 按客户目标选择所需能力，负责工具调用、资源查询和参数填写，向客户说明业务选择与结果。

开通或更新协议、申请资质和签名、引流录入、创建群发任务，默认通过表单办理：
优先 MCP App，无 MCP 或未显示时使用网页，网页不可用时按业务回退。
客户指定交互方式时沿用其选择；入口及回退条件见 [表单展示](references/form-display.md)。

查询使用 `execute_sms_read_action(action, params)`，传接口原生参数；预览和已确认的写操作
使用相应 read/write 工具的 `argv`。无 MCP 时运行
`python3 -E -B <skill目录>/scripts/sms_cli.py`，原生查询为
`api-read --action <Action> --params '<JSON>'`。环境由安装包固定，参数可查 `--help`。
创建表单或执行 read/write 工具时，Agent 自报运行产品名到顶层 `platform`；CLI 在命令前传
`--platform <产品名>`（如豆包工作、Codex）。无明确宿主信息填 `unknown`，不要按模型名或发布渠道猜测。

资质材料、号码名单和凭据留在私密表单及本机，对话展示摘要。
查询保留业务字段和审核维度，由 Agent 按客户问题解释；写操作按当前预览确认，结果未知时先查询核实。
群发名单的具体列名取自当前模板的 CSV 示例，先取得示例再解释填写方式，查询方法见群发参考。

按任务读取：

| 任务 | 参考 |
| --- | --- |
| 查询资质、消息组、签名及审核状态，申请模板 | [application-contracts.md](references/application-contracts.md) |
| 申请签名、核对名称与资质、补充证明材料 | [signature-form.md](references/signature-form.md) |
| 新建或补充资质、上传证件 | [qualification-materials.md](references/qualification-materials.md) |
| 签名与模板报备进度、运营商状态 | [report-status.md](references/report-status.md) |
| 模板查询、发送准备、发送记录、统计 | [delivery-contracts.md](references/delivery-contracts.md) |
| 群发准备、表单与任务结果 | [batch-form.md](references/batch-form.md) |
| 带链接或联系号码的申请、发送与排查 | [traffic-driving.md](references/traffic-driving.md) |
| 组合任务 | [workflows.md](references/workflows.md) |
| 鉴权、登录或 CLI 未检出 | [auth-setup.md](references/auth-setup.md) |
| 开通短信、查询开通状态或账号不可用 | [service-onboarding.md](references/service-onboarding.md) |
| 写操作授权、结果未知 | [rules.md](references/rules.md) |
| 接口与错误码 | [actions.md](references/actions.md) |
