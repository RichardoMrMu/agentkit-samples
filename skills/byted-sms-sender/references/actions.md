# 调用与错误处理

查询使用 `execute_sms_read_action(action, params)`；无 MCP 时用 `api-read --action <Action> --params '<JSON>'`。
参数保留原生名称与类型，身份由客户端鉴权。申请参数见[申请契约](application-contracts.md)，
发送与统计见[发送契约](delivery-contracts.md)，群发见[群发流程](batch-form.md)，引流见[引流能力](traffic-driving.md)。

## 调用与结果

消息组ID取接口 `subAccountId`，名称仅用于展示。`List/list` 为当前页，`Total/total` 为筛选总数，
说明实际读取范围；保留业务维度和长文本，按客户问题解释。资质校验和上传由私密表单处理。
凭据、材料和完整个人信息留在本机，输出由脚本脱敏。鉴权问题见[鉴权准备](auth-setup.md)。

## 错误与重试

- 业务错误读取 `ResponseMetadata.Error`，HTTP成功不等于业务成功；保留公开错误码、Request ID和接口说明。
- 查询的有界重试由脚本处理；写请求只发送一次。明确拒绝后按原因修改，结果未知先查询原资源，见[rules.md](rules.md)。
- `RE:0001` 先查[账号与商品状态](service-onboarding.md)；`tls_certificate_error` 由宿主修复可信证书配置。
- `1017` 表示短信验证码频率限制。权限、参数、审核和冲突错误先处理原因，再准备后续操作。

需要解释具体错误时查[接口错误码](https://www.volcengine.com/docs/6361/173288?lang=zh)或
[发送状态错误码](https://www.volcengine.com/docs/6361/173291?lang=zh)。无精确说明时保留错误与Request ID，交客户联系支持，不猜测原因。
