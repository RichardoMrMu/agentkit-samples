# 签名申请

先查询并复用已有签名、资质。新建时预填已有信息，由客户核对名称、材料和消息组范围。
**发送类型固定全选验证码、通知、营销三种，客户无需选择。**省略 `channelTypes` 时由领域补齐，显式传入必须包含全部三项。

## 打开与预填

按[表单展示](form-display.md)选择入口。MCP 用 `open_signature_application(params)`；
CLI 用 `signature-wizard --params '<JSON>' --display host`。`params` 示例：

```json
{"content":"示例企业","source":1,"signatureIdentificationID":123,"subAccounts":["group-id"],"channelTypes":["CN_OTP","CN_NTC","CN_MKT"]}
```

`signatureIdentificationID` 为已查到的可用资质整数ID，`source` 为1公司、2App、3商标；
自用/他用取资质，显式 `purpose=1/2` 须与之相符。`subAccounts` 为字符串数组，空数组表示全消息组范围。
唯一可用资质和消息组自动预填，公司默认营业执照全称；名称2–25字符，建议12字以内，签名外围括号由系统添加。
简称应连续、可识别且超过3字；页面连续匹配通过不等于后端审核通过，主体和范围未定时由客户核对。
可选 `desc`（最多300字）、`projectName`；App、商标名称使用备案/注册全称，核对权利关系。

## 材料与确认

| 来源 | 参数与材料 |
| --- | --- |
| 公司 | 引用已有资质，核对名称，可补其他证明 |
| App | `appIcp.appIcpFilling` 为备案号，备案号或备案材料至少一项 |
| 商标 | `trademark.trademarkNumber` 必填；`trademarkCn/trademarkEn` 按注册名称填写，证明文件按需补充 |
| 全部来源 | 可选更多证明，附件在私密页面选择，最多五份 |

App/商标支持 ZIP、PDF、JPG、PNG、JPEG；更多证明支持 PNG、JPG、JPEG、DOC、DOCX、GIF、ZIP。
页面不额外限制单个文件大小。上传及 URI 由页面处理，申请不携带原始字节，个人材料不进入对话。
核对申请时，名称未连续出现在营业执照企业名称中则展示风险，客户需明确接受后“强制提交”；
没有名称风险时普通确认提交。材料或资质变化后保留已填内容并重新核对。
详细要求按需查[签名审核规范](https://www.volcengine.com/docs/6361/170338)和[实名制报备](https://www.volcengine.com/docs/6361/1529695)。

## 提交结果与会话回退

读取原流程结果，先说明申请编号和实际查到的审核状态，查询方法见[签名列表](application-contracts.md#签名列表)。
`status=1` 为已受理、审核中；仍需模板准备，可同步拟文案，发送安排依据实际审核与资源进度，不承诺审核时间。

网页不可用或客户选择会话时，无附件申请用 `signature-preview/signature-submit`，MCP 两条均走 `execute_sms_write_action`。
通过 `--params '<JSON>'` 传上述原生参数，客户确认后以相同参数和当前 `--preview-digest` 提交；
名称风险另需 `--accept-risk`。页面和会话共用确认记录，已提交复用申请编号，结果未知查询原申请，不重复创建。
`signatureSubmission` 提供受理状态、编号和回查结果；参数、资质或身份改变后重新预览，见[rules.md](rules.md)。
