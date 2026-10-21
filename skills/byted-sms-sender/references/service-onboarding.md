# 国内短信服务开通

运行 `service-status`，或查询 `ListAllSmsProduct`，核实当前授权账号的国内短信商品 `sms`。
`available=true` 表示服务与协议已可用，其余状态按接口返回处理；`RE:0001` 时先做此查询。
实名认证由客户在当前环境的官方控制台办理；接口提示需要认证时，引导客户完成后继续。

## 表单

展示方式按 [表单展示](form-display.md) 选择。MCP 使用 `open_sms_service_onboarding`，
通过 `ensure_sms_service_onboarding_visible` 确认展示，完成后用
`get_sms_service_onboarding_result` 读取结果；CLI 入口为 `service-wizard --display host`。

页面展示国内短信的当前协议，由客户阅读并确认。首次开通先签协议再开通服务；
服务已开通且协议更新时，仅展示并签署待更新协议。提交后用 `service-status` 核实结果。

## 会话回退与控制台

网页不可用或客户明确选择会话时，用 `service-preview` 展示账号、商品、协议与计费说明。
客户明确同意协议并确认开通或重签后，执行：

```text
service-submit --preview-digest <当前摘要> --authorization-digest <客户确认的摘要>
```

参数和身份由脚本重新核对；结果待核实时，按请求编号和 `service-status` 查询。
协议使用当前官方版本，价格和结算以账号合同及官方计费说明为准。

接口未发布或权限受限时，通过 `runtime-info` 的 `console_url` 进入官方控制台办理。
完成后查询服务状态，恢复客户原来的短信任务。

官方依据：[快速入门](https://www.volcengine.com/docs/6361/1840927)、
[企业实名认证](https://www.volcengine.com/docs/6261/64935)、[国内短信计费](https://www.volcengine.com/docs/6361/67320)。
