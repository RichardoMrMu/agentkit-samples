# 签名与模板报备查询

查询当前登录账号下签名、模板在运营商的报备进度。使用 `execute_sms_read_action(action, params)`；
无 MCP 时使用 `api-read --action <Action> --params '<JSON>'`。

## 查询范围

条件齐全时直接查询报备；需要补齐时按已有资源查询：

- 签名：使用 `ListSignaturesForAgent`，传 `Signature`、`ExactMatch=true`、`Page`、`PageSize`，
  分页取得 `ChannelType`、`Industry`、`SubAccounts`，字段见[签名列表](application-contracts.md#签名列表)。
- 模板：按 `TemplateId` 查询目标模板及二级记录，取得签名、发送类型、行业和消息组，
  见[模板查询](delivery-contracts.md#模板查询与精确匹配)。

按资源返回的发送类型和行业分别查询；范围较大时结合客户用途选取，并说明实际覆盖范围。

## 报备接口

`AgentGetSignatureTemplateReportStatus` 使用 GET，版本 `2026-01-01`。
必填 `Signature`、`ChannelType`、`Industry`，行业取查询到的资源值。
`ChannelType`：`CN_OTP` 验证码、`CN_NTC` 通知、`CN_MKT` 营销。

| 可选参数 | 使用方式 |
| --- | --- |
| `SubAccount` | 目标消息组实际 ID；省略时按平台选取的默认消息组查询 |
| `TemplateId` | 查询该模板的报备情况 |
| `Content` | 查询填完变量后的实际正文，含链接或号码时使用本机完整原值；同时传入时优先于模板正文 |
| `Carrier` | `中国移动`、`中国联通`、`中国电信`；省略时查询三家 |

## 结果说明

结果为 `CMCC`、`CUCC`、`CTCC` 下的 `Status` 和 `StatusDescription`：
`1` 报备中、`2` 报备成功、`3` 过期风险。它是平台按当前通道计算的报备汇总。
按本次查询的发送类型、行业、消息组和运营商展示，未明确的状态列为待核实。
平台审核看签名和模板记录，运营商报备看本接口；发送受理由提交接口确认，实际送达由回执确认。
