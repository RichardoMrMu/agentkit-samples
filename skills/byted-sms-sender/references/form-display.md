# 表单展示

默认使用下表的表单入口：优先 MCP App，未显示或无 MCP 时使用宿主网页预览或系统浏览器。
客户已指定交互方式时沿用其选择。

| 任务 | MCP 打开工具 | CLI 网页入口 |
| --- | --- | --- |
| 开通或更新协议 | `open_sms_service_onboarding` | `service-wizard` |
| 创建资质 | `open_qualification_application` | `qualification-wizard` |
| 申请签名 | `open_signature_application` | `signature-wizard` |
| 录入链接或号码 | `open_traffic_driving_creation` | `traffic-wizard --kind link\|phone` |
| 创建群发任务 | `open_batch_task_creation` | `batch-wizard` |

## MCP 与网页

打开 MCP 表单后保存 `flowId`，用对应的 `ensure_*_visible` 确认展示。
系统浏览器可通过 `*_in_browser` 继续同一流程；改用宿主网页预览时，按
`preferredFallback` 结束旧流程，再运行 CLI 入口。

CLI 在持续运行的前台任务中执行，保存任务句柄。支持宿主网页预览时使用
`--display host`，将 `QUALIFICATION_DISPLAY` 中的私密 URL 原样交给展示工具；
使用系统浏览器时选择 `--display browser`。业务参数见各表单文档。

`displayStatus=opened` 或 CLI 的 `LOCAL_FORM_STATUS {"state":"ready"}` 表示页面已就绪。
等待客户操作并读取原流程的最终结果，切换网页展示工具时复用原 URL。

## 业务回退

网页不可用时，结束原流程并根据结果使用该业务的会话、文件或控制台入口；群发文件回退以
`fallbackAllowed=true` 为回退依据。客户取消时结束本次操作，结果未知时先查询原请求。
材料和名单由私密页面或本机脚本处理，聊天交接业务信息、文件路径和确认。
各业务的回退方式见其参考文档，授权与结果处理见 [rules.md](rules.md)。
