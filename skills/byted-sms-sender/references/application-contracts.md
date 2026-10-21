# 资源查询与模板申请

资质材料办理见[资质向导](qualification-materials.md)，创建签名见[签名申请](signature-form.md)。

## 资质与消息组

| 查询 | 参数与结果 |
| --- | --- |
| `GetSignatureIdentificationList` | `pageIndex`、`pageSize`，可选整数 `id`、字符串 `materialName`、整数数组 `status`；返回 `list/total` |
| `GetSubAccountListForAgent` | `pageIndex`、`pageSize`，可选 `subAccountName`、`subAccount`、`project`、`allStatus`；ID 为 `subAccountId` |
| `GetSubAccountDetail` | `subAccount`；返回消息组状态、`enabledChannelType[].value` 和行业配置 `channelTypeToIndustryConfig` |

资质的返回状态是 `auditStatus`：1 审核中、2 拒绝、3 通过；申请签名选择通过且 `usable=true` 的资质。
请求 `status` 是筛选数组。后端最多取500条候选再分页，说明实际覆盖范围；保留审核意见、可用性等业务信息。
消息组默认只查启用组，停用或删除状态需 `allStatus=true`；`list-message-groups` 默认完整分页，其他选项见 `--help`。

## 签名列表

使用 `ListSignaturesForAgent`，按短信类型、行业、审核状态和消息组范围逐行解释。
同一“签名＋短信类型＋行业＋审核状态”聚合为一行，消息组合并到 `SubAccounts`。

```json
{"action":"ListSignaturesForAgent","params":{"Signature":"示例企业","ExactMatch":true,"Page":1,"PageSize":100}}
```

`Page` 从1开始，`PageSize` 为1–100；仅传客户要求的筛选：`Signature`、`ExactMatch`、`ProjectName`、
`SubAccounts`、`ChannelTypes`、`Industries`、`Statuses`。后四项为数组，状态用整数，其余用字符串。
完整名称用 `ExactMatch=true`，否则按前缀查询；全部状态不传 `Statuses`。按返回 `Total` 继续分页。
CLI 用 `list-signatures`，参数见 `--help`。

`SubAccounts=[]` 表示该行业和短信类型下全消息组适用；`OrderID` 保留字符串，时间为 Unix 秒。
`StatusDescription`、`AuditOpinion` 和 `Usable` 按原值解释；意见为空时说明未提供原因。
常见状态：1 审核中、2 拒绝、3 通过、5 免审、16 等待资质审核。`Usable` 只表示该行审核通过或免审，
运营商进度另查[报备状态](report-status.md)，发送准备以模板记录和提交结果为准。
`ListSignatureForAgent` 为汇总查询；其 `SubAccounts` 为空或含 `*`/`All` 表示全消息组范围。

## 签名申请

参数、材料、预览与提交统一见[签名申请](signature-form.md)。创建签名固定全选验证码、通知、营销三种类型。

## 预览与提交

模板用 `template-preview --params '<JSON>'` 预览，经客户确认后，以相同原始参数和
`--preview-digest <digest>` 调用 `template-submit`；MCP 分别使用 read / write 工具。
参数、身份或资源改变后重新预览。脱敏展示值不能代替本机原值，结果未知按[rules.md](rules.md)核实。

## 模板申请

按完整内容的目的选择：`CN_OTP` 身份验证、`CN_NTC` 事务通知、`CN_MKT` 商品或服务推广。
文案只使用客户给定事实，未知信息先询问或省略。
统一调用 `ApplySmsTemplateV2ForAgent`，预览会读取账号配置、核对固定引流资源并展示审核风险。

```json
{"content":"您预约的服务时间为${time}，请准时到店。","channelType":"CN_NTC","area":"cn","name":"预约提醒","signatures":["示例企业"],"subAccounts":["group-id"],"templateParams":[{"name":"time","paramType":1}]}
```

`signatures`、`subAccounts` 是字符串数组。`templateParams` 每项 `name` 对应正文 `${name}`，同名定义一次；
`paramType`：1 普通、2 链接、3 号码、4 Path、5 超级变量，无变量传 `[]`。
名称最多50字符且不超过100字节，`desc` 最多300字；变量名使用英文、数字及下划线。
验证码模板包含一个普通变量及验证码用途词；通知/营销占位符用文字或标点分隔，最多10次。
链接、号码、Path 数量分别最多1、2、1。小微账号通知用无变量方案，营销变量读取账号 `mktParam`，
营销准入、退订格式及最终审核以服务端为准。

可选字符串 `project`、`desc`；`shortUrlConfig` 的 `isEnabled`、`belong`、`isNeedClickDetails`
为字符串，`uaCheckStrategy` 为整数。引流变量绑定、缺少资源的录入见[引流能力](traffic-driving.md)。
返回 `templateId` 表示申请已受理，当前审核状态查 `ListSmsTemplateForAgent`，正文、变量及适用范围查
`ListSecondTemplate`。换签名需按新签名重新申请并取得新模板ID；失败按原错误调整，见[错误处理](actions.md)。
