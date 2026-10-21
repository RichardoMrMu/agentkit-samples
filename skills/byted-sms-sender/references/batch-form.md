# 群发任务

## 准备

用 `list-templates` 结合客户用途选模板，可加 `--template-id/--signature/--sub-account`，
重复 `--keyword` 匹配名称、正文和用途；使用返回的 `Signature`。
模板 `SubAccounts` 为具体ID时按范围选择，`All`/`*` 时用 `list-message-groups` 查启用组。
沿用已有可用选择，唯一候选可采用，多项请客户选择。
`TaskFields` 声明 content 时准备任务正文，其余变量由名单逐行填写，页面自动预检正文。
单独预检用 `batch-content-check`，参数见 `--help`。

指导名单前，查询 `TemplateUploadDemoForAgent`，传当前 `subAccount/templateId`；
CLI 用 `batch-template-demo --sub-account <ID> --template-id <模板>`。
返回 `value` 是CSV原文，保留首行实际列名和顺序，用中文解释各列。未取得时请客户在表单下载示例；
模板改变后重新获取，不猜表头。

发送时间未说明时确认立即或定时，已明确则沿用。活动时间与发送时间分别处理，
相对时间转为 Asia/Shanghai 具体日期时刻并告知；不足时补问，限制见[发送契约](delivery-contracts.md)。

## 表单与确认

使用 `open_batch_task_creation`，传 `subAccount/taskName/signature/templateId`，按 `TaskFields` 选填 `content`：
立即发送 `scheduled=false`、省略 `sendTime`；定时发送 `scheduled=true`，`sendTime` 为带 `+08:00` 的 ISO 8601。
展示、句柄保存与回退统一按[表单展示](form-display.md)处理。
无 MCP 时用 `batch-wizard --sub-account <ID> --task-name <名称> --signature <签名> --template-id <模板> --display host`，
按需加 `--content`；立即用 `--immediate`，定时用 `--scheduled --send-time <ISO时间>`。

客户上传按当前示例整理的CSV并核对时间。页面先创建待确认任务，展示有效号码和重复数，
客户勾选确认后启动同一 taskId。`BatchOnly=true` 仅支持群发；其他模板可在页面试发并按 Message ID 刷新结果。
引流检查可传 `checkTraffic=true`（CLI `--check-traffic`），见[引流能力](traffic-driving.md)。

## 结果与取消

MCP 用 `get_batch_task_creation_result`，CLI 读取原任务输出。

| 结果 | 后续处理 |
| --- | --- |
| `batch_file_validated` | 保存 taskId、subAccount，说明已创建待确认 |
| `batch_task_confirmed` | 回查同一任务并说明当前状态 |
| `outcomeUnknown=true` | 按已知任务ID核实，不重建 |
| `fallbackAllowed=true` | 原流程结束且宿主无法展示时，使用[文件回退](batch-file-fallback.md) |

按返回的 `subAccount/taskId` 查询 `GetBatchTaskDetail`，依据详情说明人数、安排和状态。
`completed=true` 只表示本机表单结束，启动以 status 和详情为准，发送与送达以日志、回执为准。
取消时先关闭表单并读结果；已有 taskId 时执行 `batch-cancel`，再用 `batch-detail` 核实状态7，两者传同一消息组及任务ID。
正文或资源变更后重新准备表单，文件和时间在当前页面调整；授权与未知结果见[rules.md](rules.md)。
