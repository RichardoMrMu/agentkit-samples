# 模板、发送与统计

## 模板查询与精确匹配

| Action | 用途与参数 |
| --- | --- |
| `ListBatchTemplatesForAgent` | 群发目录；`Page`、`PageSize`，可选 `TemplateId`、`SubAccounts`、`Signatures` |
| `ListSmsTemplateForAgent` | 普通模板查询，用于单条发送和模板状态查询 |
| `ListSecondTemplate` | 二级模板正文、变量、行业与审核状态；`project`、`templateId`、`secondTemplateId`、`signatures`，按需限定 `subAccounts` |

群发目录返回 `Total/List`，正文为 `TemplateContent`、适用组为 `SubAccounts`、用途为 `Description`。
`TaskFields` 为空时无需任务级字段；`BatchOnly=true` 只支持群发。
`list-templates` 默认完整分页，重复 `--keyword` 按名称、正文、用途做任一关键词的字面匹配，语义适配由Agent判断。
筛选 `Total` 为候选数，`ScannedTotal` 为读取数；`--page` 仅用于查看单页。

`match-template` 可辅助比较普通模板，必填 `--content`、`--signature`、`--sub-account`，可选 `--channel-type`。
`templateRecords` 是原始记录，`matchingRecords` 是正文和类型命中的记录；完整查询的
`classification=none/single/ambiguous` 表示零/一/多命中。`complete=false` 表示未查全，结合 `errors` 补查。
匹配不代表可以发送，结合客户用途及实际范围选择模板。

## 报备与发送准备

按签名、模板、短信类型、行业和消息组核实资源，运营商进度见[报备查询](report-status.md)。
模板 `SubAccounts` 的具体ID限定范围；`All`/`*` 时从 `GetSubAccountListForAgent` 返回的启用组中选实际ID。
短信类型取模板 `ChannelType`。读取二级记录的 `content`、`templateParams`、`industry`、`reviewStatus`、`reason`，
时间 `createdAt/updatedAt` 为 Unix 秒。正文、变量和类型一致时可共享预览，有差异先核对；空结果不能作为预览依据。
审核进度按每条记录解释，发送能否受理由提交接口判断。

## 单条发送

`SendSmsForAgent` 必填 `SubAccount`、`Signature`、`TemplateId`、逗号分隔的 `Mobiles`；
`TemplateParam` 是紧凑 JSON 字符串，变量集合须与模板一致。按当前预览确认后提交，返回 Message ID 仅表示受理。
试发及群发见[群发表单](batch-form.md)，未知结果见[rules.md](rules.md)。

## 发送日志与统计

默认查询当前主账户全部消息组；仅客户明确限定时传范围。统计省略 `SmsAccount/Project`，记录省略 `SubAccount`。
`ListSmsSendLogForAgent` 传 `Page/PageSize`，按需传 `MessageId`、`SubAccount`、`FromTime`、`ToTime`、
`Mobile`、`TemplateId`、`Signature`。请求时间为 Unix 秒，返回 `SendTime/ReceiptTime` 为 Unix 毫秒。
CLI `send-status --message-id` 可加 `--from-time/--to-time`；试发按本次请求开始至当前时间精确匹配 Message ID。
保留错误码、类型、说明及时间；缺少 `Status` 时状态未知，明确返回时：1 等待回执、2 失败、3 成功回执。
完整手机号和正文留在私密处理流程。

聚合统计使用 `GetTotalSendCountStatV4ForAgent`：

| 参数 | 含义 |
| --- | --- |
| `StartTime/EndTime` | Unix 秒，最长90天 |
| `Project/SmsAccount` | 项目/消息组字符串 |
| `ChannelType/Signature/TemplateId` | 字符串数组 |
| `StandardErrorCode` | 失败码数组，不传空字符串或成功码 `0` |
| `IsDomestic`、`NotSplit` | 国内为1；`NotSplit=false` 拆分计数，true 未拆分计数 |
| `DateGroup/SmsAccountGroup/ChannelGroup/SignatureGroup/TemplateIdGroup/StandardErrorCodeGroup` | 对应分组，1开启 |

时间按 Asia/Shanghai：昨天零点起的实时数据用 `[StartTime,EndTime)`；更早只有日粒度，
整日范围传首日 `00:00:00` 至末日 `23:59:59`，按完整自然日展示。小时或不完整日期请求需说明实际覆盖粒度，
跨实时/历史时分别说明范围。
直接使用服务端 `DataPoints/DataPointsByDate` 与总量汇总，按模板或错误码分析使用对应 Group 参数；
记录分页只用于消息级证据，`Count` 缺失保持未知。
提交成功率=`TotalSendSuccessCount/TotalAllSendCount`，回执成功率=`TotalReceiptSuccessCount/TotalSendSuccessCount`。
说明分子、分母及拆分口径；`TotalSendCount/TotalNoReceipt72HourCount` 按接口含义单列。
分母为零、字段缺失或分页不全时说明限制，聚合条数不能当作消息记录数。

## 群发上传与任务

准备名单、正文预检、表单及启动见[群发流程](batch-form.md)，表单不可用时按[文件回退](batch-file-fallback.md)处理。
CSV 使用当前模板的 `TemplateUploadDemoForAgent` 示例，保留其表头；文件上限100万个手机号、50 MB，
本机预检不改原CSV，最终有效数量和去重以服务端为准。
定时限未来30天，国内默认 Asia/Shanghai 的08:00–21:30，账号例外及最终限制由服务端校验；
进入发送前一分钟截止区间后不能取消。

`GetBatchTaskDetail` 传 `subAccount/taskId`，是已知ID的权威对账来源。
`GetBatchTaskList` 传 `subAccount/pageIndex/pageSize`，可选 `taskName/signature/templateId`；状态2可能不在列表中。
任务状态：0 初始化、1 草稿、2 已校验待授权、3 已接受启动、4 准备中、5 发送中、
6 任务完成（不代表逐条送达）、7 已取消、8 失败、9 强制终止。
创建结果 `taskId/totalCount/dupCount` 按服务端原值说明，缺失计数不从本机补齐。
启动 `ConsentBatchTask` 仅允许最新状态2和客户确认的同一摘要，取消用 `DeleteBatchTask`；均传 `subAccount/taskId`。
写入响应丢失时查询同一任务，不换标识重建。正文、资源和范围变化后重新预览，授权见[rules.md](rules.md)。
