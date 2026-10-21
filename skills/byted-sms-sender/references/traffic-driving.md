# 引流资源与发送准备

已有合适模板可直接复用；公共大变量模板拒绝链接和联系方式，可使用客户自有模板。
录入、模板审核、运营商报备和送达分别核实。

## 查询

使用 `execute_sms_read_action(action, params)`；无 MCP 时用 `api-read`，传原生参数。

| 目标 | Action | 参数 |
| --- | --- | --- |
| 链接及备案信息 | `GetTobTrafficDrivingLinkListForAgent` | 可选 `id`、`link`、`jumpLink`、`useVolcLink` |
| 号码及所属企业 | `GetTobTrafficDrivingPhoneListForAgent` | 可选 `id`、`number` |
| 按资源找模板 | `ListRelationTemplateForAgent` | `signature`、`number`、`link`、`jumpLink` 至少一项非空 |
| 检查正文资源 | `CheckTrafficDrivingExist` | `content` 传填完变量后的正文，`useVolcLink` 取模板配置 |
| 查看组合报备 | `ListThirdTemplate` | `secondTemplateId`；可选 `phoneId`、`linkId`、`subContentId` |

## 录入与预填

使用 `open_traffic_driving_creation`，`kind=link|phone`；无 MCP 时用
`traffic-wizard --kind link|phone --params '<JSON>'`。展示和回退见[表单展示](form-display.md)。
`params.data` 为预填对象数组，客户核对、补缺并确认提交。

| 资源 | 可预填字段 |
| --- | --- |
| 自有链接（`useVolcLink=false`） | `protocol`、`link`、`linkDomainIcp`、`linkDomainIcpBody` |
| 火山短链（`useVolcLink=true`） | `jumpLink`、`jumpLinkDomainIcp`、`jumpLinkDomainIcpBody`；文案链接由服务生成 |
| 号码 | `numberType`、`number`、`company`、`numberProvince`、`numberCity` |

`params` 示例：

```json
{"data":[{"useVolcLink":false,"protocol":"https","link":"https://www.example.com/notice","linkDomainIcp":"已核实的备案号","linkDomainIcpBody":"已核实的主办单位"}]}
```

`protocol` 取 `http`、`https`、`缺省`；完整链接保留路径和查询参数，域名和 Path 由表单生成。
`numberType`：1 手机、2 400、3 700、4 800、5 95/96、6 12、7 固定电话；省市选填。
手机号所属人及证件只在私密页面填写，其他号码填写所属企业。

备案只查域名（如 `www.example.com.cn` 查 `example.com.cn`）。优先在[工信部](https://beian.miit.gov.cn/)选“网站”，核实备案号和完整主办单位；可靠的同域名记录可复用。
访问受限时查官网渲染页脚及备案链接，注明实际来源；搜索无结果不等于未备案。
核实后按上表预填 `params.data` 再开表单，仅未查到、受限或冲突项留空，请客户补充。
文案和跳转链接分别核对；网站、APP、公安备案不混用，备案主体不以账号主体或运营方代替，主体不同不推断审核结果。

录入类型与运营商可报备类型可能不同，限制见[官方引流说明](https://www.volcengine.com/docs/6361/1856270)。
用 `get_traffic_driving_creation_result` 查结果：`traffic_created` 已写入并回查；
`traffic_created_unverified` 已写入待核实；`traffic_creation_outcome_unknown` 提交结果未知。
后两者按资源和请求编号继续核实；关闭表单不删除远端记录。

## 模板绑定与正文检查

模板预览、确认与提交见[模板申请](application-contracts.md)；缺少资源时先录入并回查。
固定链接和号码自动识别；链接/号码变量的 `paramType` 分别为 2/3，按二维数组绑定资源：

```json
{"templateParams":[{"name":"url","paramType":2}],"templateTrafficDriving":[[{"name":"url","paramType":2,"id":123}]]}
```

绑定可用资源 ID 或完整原值，火山短链取跳转链接。ID 与内容须一致，脱敏展示值不能用于绑定；
资源变化后重新预览确认，动态 URL 匹配规则以服务端为准。

`traffic-check --content '<实际正文>'` 检查单条；加 `--file <本机CSV路径>` 按变量逐行渲染，
CSV 只需正文引用的变量列。模板使用火山短链时加 `--use-volc-link`，其他选项见 `traffic-check --help`。
CSV 本机读取，实际正文提交接口检查，输出脱敏；`complete=false` 表示未完整覆盖。
群发表单可传 `checkTraffic=true`（CLI：`--check-traffic`）；任务后续操作见[群发参考](batch-form.md)。

报备状态见[报备查询](report-status.md)，发送见[发送契约](delivery-contracts.md)，授权与对账见[rules.md](rules.md)。
