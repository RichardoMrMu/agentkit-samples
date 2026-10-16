# RuntimeTemplate 客户端契约记录

## 基线

- 上游仓库：`akx`
- 远端引用：`origin/master`
- Commit：`c8b1623eae50d889d5e87823b6906fe84ed333f9`
- 获取日期：2026-10-16
- 契约文件：`docs/harness-infra/api/runtime-template-api.md`
- API Version：`2025-10-30`

该上游快照在获取时仍保留“TOP 发布和端到端尚未验证”的说明。发布完成后，
本示例已按下述记录完成独立线上验证。

## 火山引擎线上验证

- 验证时间：2026-10-16
- Provider/Region：Volcengine `cn-beijing`
- Endpoint：`open.volcengineapi.com`
- 测试模板：`rt_db8tlo9sam7s72qgi5ug`（测试后已删除）
- Runtime Role：未显式传入，成功复用 `AgentKitDefaultRuntimeAccess`
- Create：观察到 `Building -> Ready`
- Get：校验 Resources、Envs、Runtime、Meta 和服务默认镜像
- List：以 `MaxResults=1` 遍历真实 `NextToken` 分页
- Update：`Description` 和 `Meta` 更新成功，状态保持 `Ready`
- 负向 Update：受管 Runtime 的 `Resources` 更新返回 `409 Conflict`
- Delete：成功结果仅含 RuntimeTemplateId
- 删除后验证：Get 返回 `404 ResourceNotFound`，List 不再包含测试模板

BytePlus 路由、签名、环境变量和 dry-run 由离线测试覆盖，本次未使用 BytePlus
账号执行线上资源测试。

## 路由和响应

TOP 请求统一发送到：

```text
POST /?Action=<Action>&Version=2025-10-30
```

请求字段使用 PascalCase。成功响应使用
`ResponseMetadata`/`Result`，错误位于 `ResponseMetadata.Error`。列表分页字段为
`MaxResults` 和 `NextToken`。

## 创建约束

- `Name` 和预先存在的 `RoleName` 必填。
- `Resources` 使用 `CpuMilli`、`MemoryMiB`、`MinReplicas`、
  `MaxReplicas`；注意字段名是 `MemoryMiB`，不是 `MemoryMb`。
- `Runtime` 显式出现时必须包含白名单内的 `StartCommand`。
- `Envs` 只能包含普通字符串配置。服务明文保存并返回这些值。
- `ClientToken` 去除首尾空白后最长 64 个 ASCII 字符；允许中间空格，
  禁止控制字符。
- 公开接口固定创建并管理新的 AGENTKIT resident Runtime。请求不得包含
  `Provider`、`ProviderConfig`、`RuntimeId` 或引用已有 Runtime 的等价字段。

示例默认总是发送完整 `Resources`，但不发送任何执行器选择或已有 Runtime
引用字段。

## 查询和分页

- Get 仅发送 `RuntimeTemplateId`。
- List 发送 `MaxResults`，后续页原样回传 `NextToken`。
- 状态包括 `Building`、`Ready`、`Updating`、`Failed`、`Deleting`。
- 删除完成后 Get 返回 404，List 不再返回该模板。

## 更新约束

契约一般允许 `Description`、`Resources`、`ImageUrl`、`Runtime` 和 `Meta`
作为顶层 UpdateMask，但本接口创建的受管 Runtime 模板只能更新
`Description` 和 `Meta`。因此示例仅开放：

```json
{
  "RuntimeTemplateId": "rt_example",
  "UpdateMask": ["Description", "Meta"],
  "Description": null,
  "Meta": null,
  "ClientToken": "template-update-clear-001"
}
```

`null` 用于清空。`Meta` 是整体替换，不按 key 合并。`Envs` 不支持更新；
需要变更时创建替代模板。

## 删除约束

Delete 必须发送 `RuntimeTemplateId` 和非空 `Reason`，可发送
`ClientToken`。成功 `Result` 只能包含：

```json
{"RuntimeTemplateId": "rt_example"}
```

删除前必须清理活动 Session 和 Environment 引用。示例的 `--wait` 会继续
调用 Get，直到观察到 404。

## 本地安全边界

- TOP 凭据只从环境变量读取。
- JSON 输出会隐藏凭据字段及整个 `Envs` 的值。
- `config.yaml` 固定为 `0600`，运行时只写入 Endpoint、RuntimeTemplateId、
  Status。
- `--dry-run` 不读取凭据、不发送请求、不写状态。
